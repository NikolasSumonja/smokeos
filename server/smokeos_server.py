#!/usr/bin/env python3
"""
SmokeOS server - dashboard + rig API.

  python3 smokeos_server.py setup            create the admin account + 2FA
  python3 smokeos_server.py add-rig NAME     create a rig and print its secret token
  python3 smokeos_server.py run              start the server (127.0.0.1:8420)
  python3 smokeos_server.py updates --repo OWNER/smokeos [--github-token TOKEN]
                                             where to look for new releases

Expose it privately with:  tailscale serve --bg 8420
"""
import argparse
import base64
import getpass
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import struct
import sys
import threading
import asyncio
import shlex
import shutil
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

BASE = Path(__file__).resolve().parent
REPO_ROOT = BASE.parent                     # the unpacked smokeos/ folder
sys.path.insert(0, str(REPO_ROOT / "common"))
import ed25519  # noqa: E402

DATA = Path(os.environ.get("SMOKEOS_DATA", BASE / "data"))
DB_PATH = DATA / "smokeos.db"
UPD_DIR = DATA / "updates"
STATIC = BASE / "static"

OFFLINE_AFTER = 60            # seconds without a report -> rig offline
HISTORY_DAYS = 7
SESSION_SECS = 7 * 24 * 3600
LOCKOUT_FAILS = 5
LOCKOUT_SECS = 15 * 60
TEMP_ALERT = 80               # GPU core C
UPDATE_EVERY = 30 * 60        # check GitHub every 30 min
GITHUB_API = os.environ.get("SMOKEOS_GITHUB_API", "https://api.github.com")


def read_version():
    try:
        return (REPO_ROOT / "VERSION").read_text().strip()
    except Exception:
        return "0.0.0"


VERSION = read_version()

COMMANDS = {"restart_miner", "stop_miner", "start_miner", "reboot",
            "set_power_limit", "lock_core", "reset_clocks", "update_agent", "apply_oc",
            "apply_flightsheet", "set_autofan", "open_shell"}

# Flight sheet -> miner command. Paths are where the SmokeOS USB image installs each miner.
MINERS = {
    "krig": {"label": "KRig", "bin": "/opt/smokeos/miners/krig/krig-miner", "algo": False,
             "tpl": "{bin} --url {pool} --user {login}", "api": ""},
    "srbminer": {"label": "SRBMiner", "bin": "/opt/smokeos/miners/srbminer/SRBMiner-MULTI", "algo": True,
                 "tpl": "{bin} --disable-cpu --algorithm {algo} --pool {pool} --wallet {login} --api-enable --api-port 21550",
                 "api": "http://127.0.0.1:21550"},
    "t-rex": {"label": "T-Rex", "bin": "/opt/smokeos/miners/t-rex/t-rex", "algo": True,
              "tpl": "{bin} -a {algo} -o {pool} -u {login} -p x --api-bind-http 127.0.0.1:4067",
              "api": "http://127.0.0.1:4067/summary"},
    "custom": {"label": "Custom command", "algo": False, "tpl": "{custom}", "api": ""},
}


def fs_command(fs):
    m = MINERS.get(fs["miner"])
    if not m:
        raise ValueError("unknown miner")
    if fs["miner"] == "custom":
        return fs["custom"].strip(), ""
    cmd = m["tpl"].format(bin=m["bin"], pool=shlex.quote(fs["pool"]), login=shlex.quote(fs["login"]),
                          algo=shlex.quote(fs["algo"] or ""))
    if fs["extra"]:
        cmd += " " + fs["extra"].strip()
    return cmd, m["api"]


def clean_fs(b):
    fs = {k: str(b.get(k, "") or "").strip() for k in ("name", "coin", "miner", "pool", "login", "algo", "extra", "custom")}
    if not fs["name"] or len(fs["name"]) > 40:
        raise ValueError("Give the flight sheet a name (max 40 characters)")
    if fs["miner"] not in MINERS:
        raise ValueError("Pick a miner")
    if fs["miner"] == "custom":
        if not fs["custom"]:
            raise ValueError("Enter the full miner command")
    else:
        if not fs["pool"] or not fs["login"]:
            raise ValueError("Pool and wallet/login are required")
        if MINERS[fs["miner"]]["algo"] and not fs["algo"]:
            raise ValueError(f"{MINERS[fs['miner']]['label']} needs an algorithm (e.g. pearlhash, progpowz)")
    for k, v in fs.items():
        if len(v) > 600 or "\n" in v:
            raise ValueError(f"{k} is too long or has a line break")
    return fs


# interactive shells: browser <-> server <-> agent, relayed through these buffers
SHELLS = {}
SHELL_IDLE = 30 * 60

# per-GPU overclock fields: (min, max); 0 always means "stock / auto"
OC_FIELDS = {"core_lock": (300, 3200), "core_offset": (-1000, 1000), "mem_offset": (-4000, 4000),
             "power_limit": (50, 600), "fan": (20, 100)}


def clean_oc(profile):
    out = {}
    for idx, vals in (profile or {}).items():
        if not str(idx).isdigit() or int(idx) > 31 or not isinstance(vals, dict):
            raise ValueError(f"bad GPU index {idx}")
        g = {}
        for k, (lo, hi) in OC_FIELDS.items():
            v = vals.get(k, 0)
            v = int(v) if str(v).strip().lstrip("-").isdigit() else 0
            if v != 0 and not (lo <= v <= hi) and k not in ("core_offset", "mem_offset"):
                raise ValueError(f"GPU{idx} {k} must be 0 (stock) or {lo}-{hi}")
            if k in ("core_offset", "mem_offset") and not (lo <= v <= hi):
                raise ValueError(f"GPU{idx} {k} must be between {lo} and {hi}")
            g[k] = v
        out[str(int(idx))] = g
    return out


def vtuple(v):
    try:
        return tuple(int(x) for x in str(v).lstrip("v").split("-")[0].split("."))
    except Exception:
        return (0,)

# --------------------------------------------------------------------------- db

_lock = threading.RLock()
_conn = None


def conn():
    global _conn
    if _conn is None:
        DATA.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE,
            pw_hash TEXT, totp_secret TEXT);
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER,
            expires REAL, ip TEXT);
        CREATE TABLE IF NOT EXISTS rigs(id INTEGER PRIMARY KEY, name TEXT UNIQUE,
            token_hash TEXT, created REAL, last_seen REAL, last_report TEXT);
        CREATE TABLE IF NOT EXISTS samples(rig_id INTEGER, ts REAL, hashrate REAL,
            power REAL, max_temp REAL);
        CREATE INDEX IF NOT EXISTS samples_rig_ts ON samples(rig_id, ts);
        CREATE TABLE IF NOT EXISTS commands(id INTEGER PRIMARY KEY, rig_id INTEGER, type TEXT,
            params TEXT, status TEXT, created REAL, finished REAL, result TEXT, by_user TEXT);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, rig_id INTEGER, ts REAL,
            level TEXT, message TEXT);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, ts REAL, username TEXT,
            ip TEXT, action TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS login_fails(ip TEXT PRIMARY KEY, count INTEGER, first REAL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS flightsheets(id INTEGER PRIMARY KEY, name TEXT UNIQUE, coin TEXT,
            miner TEXT, pool TEXT, login TEXT, algo TEXT, extra TEXT, custom TEXT, updated REAL);
        """)
        cols = [r[1] for r in _conn.execute("PRAGMA table_info(rigs)")]
        if "hw_id" not in cols:
            _conn.execute("ALTER TABLE rigs ADD COLUMN hw_id TEXT")
            _conn.commit()
    return _conn


def q(sql, args=(), one=False):
    with _lock:
        cur = conn().execute(sql, args)
        rows = cur.fetchall()
    return (rows[0] if rows else None) if one else rows


def x(sql, args=()):
    with _lock:
        c = conn()
        cur = c.execute(sql, args)
        c.commit()
        return cur.lastrowid


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def setting(key, default=None):
    r = q("SELECT value FROM settings WHERE key=?", (key,), one=True)
    return r["value"] if r else default


def set_setting(key, value):
    if value is None:
        x("DELETE FROM settings WHERE key=?", (key,))
    else:
        x("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, str(value)))


def farm_key():
    k = setting("farm_key")
    if not k:
        k = "farm_" + secrets.token_urlsafe(24)
        set_setting("farm_key", k)
    return k


DISCOVERY_PORT = 8429


def home_on():
    return setting("home_auto_add") == "on"


_LOCAL_NETS = None


def local_networks():
    """The networks this machine is directly on (e.g. 192.168.1.0/24)."""
    global _LOCAL_NETS
    if _LOCAL_NETS is None:
        import ipaddress
        import re as _re
        import subprocess as _sp
        nets = []
        try:
            out = _sp.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True, timeout=5).stdout
            for cidr in _re.findall(r"inet (\d+\.\d+\.\d+\.\d+/\d+)", out):
                n = ipaddress.ip_network(cidr, strict=False)
                if not n.is_loopback:
                    nets.append(n)
        except Exception:
            pass
        if not nets:                                   # Windows / no `ip`: assume /24 around our addresses
            import socket as _s
            try:
                for addr in _s.gethostbyname_ex(_s.gethostname())[2]:
                    if not addr.startswith("127."):
                        nets.append(ipaddress.ip_network(addr + "/24", strict=False))
            except Exception:
                pass
        _LOCAL_NETS = nets
    return _LOCAL_NETS


def is_lan_peer(ip):
    """A device on the home network: private address or same subnet as this server.
    Loopback is excluded (that's Tailscale serve / a local proxy, not a rig on the LAN)."""
    import ipaddress
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if a.is_loopback or a.version != 4:
        return False
    if a.is_private and not ipaddress.ip_network("100.64.0.0/10").supernet_of(ipaddress.ip_network(ip + "/32")):
        return True
    return any(a in n for n in local_networks())


def discovery_responder(http_port):
    """Answer 'SMOKEOS_DISCOVER' broadcasts so rigs on the home network find this server."""
    import socket as _s
    sock = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
    sock.setsockopt(_s.SOL_SOCKET, _s.SO_REUSEADDR, 1)
    try:
        sock.bind(("0.0.0.0", DISCOVERY_PORT))
    except OSError as e:
        print("discovery: can't listen on UDP", DISCOVERY_PORT, e, file=sys.stderr)
        return
    while True:
        try:
            data, addr = sock.recvfrom(512)
            if data.startswith(b"SMOKEOS_DISCOVER") and home_on():
                sock.sendto(json.dumps({"smokeos": 1, "port": http_port, "version": VERSION}).encode(), addr)
        except Exception as e:
            print("discovery:", e, file=sys.stderr)
            time.sleep(1)


def audit(user, ip, action, detail=""):
    x("INSERT INTO audit(ts,username,ip,action,detail) VALUES(?,?,?,?,?)",
      (time.time(), user, ip, action, detail))

# ---------------------------------------------------------------- crypto helpers


def hash_pw(pw):
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1)
    return "scrypt$" + salt.hex() + "$" + h.hex()


def check_pw(pw, stored):
    try:
        _, salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1)
        return hmac.compare_digest(calc.hex(), h)
    except Exception:
        return False


def totp(secret, t=None, step=30, digits=6):
    key = base64.b32decode(secret + "=" * ((8 - len(secret) % 8) % 8))
    counter = int((t if t is not None else time.time()) // step)
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    code = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 10 ** digits
    return f"{code:0{digits}d}"


def totp_ok(secret, code):
    code = (code or "").replace(" ", "").strip()
    if not code.isdigit():
        return False
    now = time.time()
    return any(hmac.compare_digest(totp(secret, now + d * 30), code) for d in (-1, 0, 1))

# ----------------------------------------------------------------------- updates
UPD = {"checking": False, "last_check": None, "error": None, "latest": None, "notes": ""}


def signing_pub():
    try:
        return (REPO_ROOT / "signing.pub").read_text().strip()
    except FileNotFoundError:
        return None


def _gh_req(url, accept="application/vnd.github+json", token=None):
    h = {"Accept": accept, "User-Agent": "SmokeOS", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = "Bearer " + token
    return urllib.request.Request(url, headers=h)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _gh_download(asset, token, dest):
    """Public repos: plain download. Private repos: API + token, then follow the
    storage redirect *without* the token (GitHub rejects it there)."""
    if token:
        try:
            resp = urllib.request.build_opener(_NoRedirect).open(
                _gh_req(asset["url"], "application/octet-stream", token), timeout=60)
        except urllib.error.HTTPError as e:
            if e.code not in (301, 302, 303, 307, 308):
                raise
            resp = urllib.request.urlopen(urllib.request.Request(e.headers["Location"],
                                          headers={"User-Agent": "SmokeOS"}), timeout=600)
    else:
        resp = urllib.request.urlopen(urllib.request.Request(asset["browser_download_url"],
                                      headers={"User-Agent": "SmokeOS"}), timeout=600)
    with resp as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


def release_files(ver):
    name = f"smokeos-{ver}.tar.gz"
    return UPD_DIR / name, UPD_DIR / (name + ".sig")


def verified(ver):
    pub = signing_pub()
    tar, sig = release_files(ver)
    return bool(pub) and tar.exists() and sig.exists() and \
        ed25519.verify_file(pub, str(tar), sig.read_text())


def check_updates():
    if UPD["checking"]:
        return
    repo, token = setting("update_repo"), setting("github_token")
    if not repo:
        UPD["error"] = "No GitHub repo set. On the server run: smokeos_server.py updates --repo OWNER/REPO"
        return
    pub = signing_pub()
    if not pub:
        UPD["error"] = "signing.pub missing next to the server (run tools/keygen.py)"
        return
    UPD["checking"] = True
    try:
        with urllib.request.urlopen(_gh_req(f"{GITHUB_API}/repos/{repo}/releases/latest",
                                            token=token), timeout=30) as r:
            rel = json.loads(r.read())
        ver = rel["tag_name"].lstrip("v")
        name = f"smokeos-{ver}.tar.gz"
        assets = {a["name"]: a for a in rel.get("assets", [])}
        if name not in assets or name + ".sig" not in assets:
            raise RuntimeError(f"release {ver} has no {name} + .sig yet (still building?)")
        UPD_DIR.mkdir(parents=True, exist_ok=True)
        tar, sig = release_files(ver)
        if not verified(ver):
            part = tar.with_name(tar.name + ".part")
            _gh_download(assets[name + ".sig"], token, sig)
            _gh_download(assets[name], token, part)
            if not ed25519.verify_file(pub, str(part), sig.read_text()):
                part.unlink(missing_ok=True)
                sig.unlink(missing_ok=True)
                raise RuntimeError(f"SIGNATURE CHECK FAILED for {ver} - release ignored")
            os.replace(part, tar)
            for f in UPD_DIR.iterdir():             # keep only the newest release
                if f.name not in (tar.name, sig.name):
                    f.unlink(missing_ok=True)
        UPD.update(latest=ver, error=None, notes=(rel.get("body") or "")[:3000])
    except urllib.error.HTTPError as e:
        UPD["error"] = f"GitHub said {e.code}" + (" (no releases yet, or private repo without token)" if e.code == 404 else "")
    except Exception as e:
        UPD["error"] = str(e)
    finally:
        UPD["checking"] = False
        UPD["last_check"] = time.time()


def update_checker():
    while True:
        check_updates()
        time.sleep(UPDATE_EVERY)


def install_server_update():
    ver = UPD.get("latest")
    if not ver or vtuple(ver) <= vtuple(VERSION):
        raise ValueError("No newer version downloaded")
    if not verified(ver):
        raise ValueError("Signature check failed - not installing")
    tar, _ = release_files(ver)
    tmp = Path(tempfile.mkdtemp())
    try:
        with tarfile.open(tar) as t:
            for m in t.getmembers():
                if m.name.startswith("/") or ".." in Path(m.name).parts:
                    raise ValueError("unsafe path in release archive")
            if hasattr(tarfile, "data_filter"):
                t.extractall(tmp, filter="data")
            else:
                t.extractall(tmp)
        src = tmp / "smokeos"
        for item in src.rglob("*"):
            rel = item.relative_to(src)
            if rel.parts[:2] == ("server", "data"):
                continue
            dest = REPO_ROOT / rel
            if item.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # exit; systemd (Restart=always) starts the new version
    threading.Timer(2, lambda: os._exit(0)).start()
    return ver


# --------------------------------------------------------------------------- app


def build_app():
    from fastapi import FastAPI, Request, HTTPException, Depends
    from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.lan = False
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def client_ip(req):
        return req.headers.get("x-forwarded-for", req.client.host if req.client else "?").split(",")[0].strip()

    def is_https(req):
        return req.url.scheme == "https" or req.headers.get("x-forwarded-proto") == "https"

    def current_user(req):
        tok = req.cookies.get("smk")
        if not tok:
            return None
        row = q("SELECT u.username FROM sessions s JOIN users u ON u.id=s.user_id "
                "WHERE s.token_hash=? AND s.expires>?", (sha(tok), time.time()), one=True)
        return row["username"] if row else None

    def need_user(req: Request):
        u = current_user(req)
        if not u:
            raise HTTPException(401, "login required")
        # simple CSRF guard: state-changing calls must come from our own JS
        if req.method not in ("GET", "HEAD") and req.headers.get("x-smokeos") != "1":
            raise HTTPException(403, "bad request origin")
        return u

    def need_rig(req: Request):
        auth = req.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            raise HTTPException(401, "no token")
        row = q("SELECT id,name FROM rigs WHERE token_hash=?", (sha(auth[7:].strip()),), one=True)
        if not row:
            raise HTTPException(401, "bad token")
        return row

    @app.middleware("http")
    async def security_headers(req, call_next):
        resp = await call_next(req)
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'")
        if is_https(req):
            resp.headers["Strict-Transport-Security"] = "max-age=31536000"
        return resp

    # ---- pages
    @app.get("/")
    def index(req: Request):
        if not current_user(req):
            return RedirectResponse("/login")
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/login")
    def login_page():
        return FileResponse(STATIC / "login.html", headers={"Cache-Control": "no-store"})

    @app.get("/manifest.json")
    def manifest():
        return FileResponse(STATIC / "manifest.json")

    @app.get("/sw.js")
    def sw():
        return FileResponse(STATIC / "sw.js", media_type="application/javascript")

    # ---- auth
    @app.post("/api/login")
    async def login(req: Request):
        ip = client_ip(req)
        f = q("SELECT count,first FROM login_fails WHERE ip=?", (ip,), one=True)
        if f and f["count"] >= LOCKOUT_FAILS and time.time() - f["first"] < LOCKOUT_SECS:
            raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
        body = await req.json()
        u = q("SELECT * FROM users WHERE username=?", (str(body.get("username", "")),), one=True)
        ok = bool(u) and check_pw(str(body.get("password", "")), u["pw_hash"]) \
            and totp_ok(u["totp_secret"], str(body.get("code", "")))
        if not ok:
            if f and time.time() - f["first"] < LOCKOUT_SECS:
                x("UPDATE login_fails SET count=count+1 WHERE ip=?", (ip,))
            else:
                x("INSERT OR REPLACE INTO login_fails(ip,count,first) VALUES(?,?,?)", (ip, 1, time.time()))
            audit(body.get("username", "?"), ip, "login_failed")
            raise HTTPException(401, "Wrong username, password or 2FA code.")
        x("DELETE FROM login_fails WHERE ip=?", (ip,))
        tok = secrets.token_urlsafe(32)
        x("INSERT INTO sessions(token_hash,user_id,expires,ip) VALUES(?,?,?,?)",
          (sha(tok), u["id"], time.time() + SESSION_SECS, ip))
        audit(u["username"], ip, "login")
        resp = JSONResponse({"ok": True})
        resp.set_cookie("smk", tok, max_age=SESSION_SECS, httponly=True,
                        secure=is_https(req), samesite="strict")
        return resp

    @app.post("/api/logout")
    def logout(req: Request, user=Depends(need_user)):
        x("DELETE FROM sessions WHERE token_hash=?", (sha(req.cookies.get("smk", "")),))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie("smk")
        return resp

    # ---- dashboard api
    def rig_view(r):
        rep = json.loads(r["last_report"]) if r["last_report"] else {}
        now = time.time()
        online = bool(r["last_seen"]) and now - r["last_seen"] < OFFLINE_AFTER
        alerts = []
        if not r["last_seen"]:
            alerts.append({"level": "warn", "msg": "Never connected"})
        elif not online:
            alerts.append({"level": "crit", "msg": f"Offline for {fmt_ago(now - r['last_seen'])}"})
        if online:
            for g in rep.get("gpus", []):
                t = g.get("temp")
                if isinstance(t, (int, float)) and t >= TEMP_ALERT:
                    alerts.append({"level": "crit", "msg": f"GPU{g.get('index')} at {t:.0f}°C"})
                if g.get("fan") == 0 and isinstance(t, (int, float)) and t > 50:
                    alerts.append({"level": "warn", "msg": f"GPU{g.get('index')} fan at 0%"})
            m = rep.get("miner", {})
            if rep.get("cooldown"):
                alerts.append({"level": "crit", "msg": "Temp guard: miner paused to cool down"})
            elif m.get("running") and m.get("uptime", 0) > 180 and not rep.get("hashrate"):
                alerts.append({"level": "warn", "msg": "Miner running but 0 hashrate"})
            elif rep.get("manage_miner") and not m.get("running"):
                alerts.append({"level": "warn", "msg": "Miner is not running"})
        latest = UPD["latest"]
        outdated = bool(latest and rep.get("agent_version") and vtuple(rep["agent_version"]) < vtuple(latest))
        return {"id": r["id"], "name": r["name"], "online": online, "last_seen": r["last_seen"],
                "report": rep, "alerts": alerts, "outdated": outdated}

    @app.get("/api/rigs")
    def rigs(user=Depends(need_user)):
        rows = q("SELECT * FROM rigs ORDER BY name")
        return {"rigs": [rig_view(r) for r in rows], "user": user, "now": time.time(),
                "version": VERSION, "latest": UPD["latest"]}

    @app.post("/api/rigs")
    async def add_rig(req: Request, user=Depends(need_user)):
        name = str((await req.json()).get("name", "")).strip()
        if not name or len(name) > 40 or not all(c.isalnum() or c in "-_ " for c in name):
            raise HTTPException(400, "Name: letters, numbers, - and _ only (max 40)")
        if q("SELECT 1 FROM rigs WHERE name=?", (name,), one=True):
            raise HTTPException(400, "A rig with that name already exists")
        tok = "smk_" + secrets.token_urlsafe(32)
        rid = x("INSERT INTO rigs(name,token_hash,created) VALUES(?,?,?)", (name, sha(tok), time.time()))
        audit(user, client_ip(req), "add_rig", name)
        return {"id": rid, "name": name, "token": tok}

    @app.post("/api/rigs/{rid}/rotate")
    def rotate(rid: int, req: Request, user=Depends(need_user)):
        r = q("SELECT name FROM rigs WHERE id=?", (rid,), one=True)
        if not r:
            raise HTTPException(404)
        tok = "smk_" + secrets.token_urlsafe(32)
        x("UPDATE rigs SET token_hash=? WHERE id=?", (sha(tok), rid))
        audit(user, client_ip(req), "rotate_token", r["name"])
        return {"token": tok}

    @app.delete("/api/rigs/{rid}")
    def delete_rig(rid: int, req: Request, user=Depends(need_user)):
        r = q("SELECT name FROM rigs WHERE id=?", (rid,), one=True)
        if not r:
            raise HTTPException(404)
        for t in ("samples", "commands", "events"):
            x(f"DELETE FROM {t} WHERE rig_id=?", (rid,))
        for k in ("oc", "fs", "af"):
            set_setting(f"{k}:{rid}", None)
        x("DELETE FROM rigs WHERE id=?", (rid,))
        audit(user, client_ip(req), "delete_rig", r["name"])
        return {"ok": True}

    @app.get("/api/rigs/{rid}/history")
    def history(rid: int, hours: float = 24, user=Depends(need_user)):
        hours = max(1, min(hours, HISTORY_DAYS * 24))
        since = time.time() - hours * 3600
        rows = q("SELECT ts,hashrate,power,max_temp FROM samples WHERE rig_id=? AND ts>? ORDER BY ts",
                 (rid, since))
        # downsample to ~300 points
        step = max(1, len(rows) // 300)
        pts = [dict(r) for r in rows[::step]]
        return {"points": pts}

    @app.get("/api/rigs/{rid}/oc")
    def get_oc(rid: int, user=Depends(need_user)):
        return {"profile": json.loads(setting(f"oc:{rid}") or "{}")}

    @app.put("/api/rigs/{rid}/oc")
    async def put_oc(rid: int, req: Request, user=Depends(need_user)):
        r = q("SELECT name FROM rigs WHERE id=?", (rid,), one=True)
        if not r:
            raise HTTPException(404)
        try:
            prof = clean_oc((await req.json()).get("profile"))
        except (ValueError, TypeError) as e:
            raise HTTPException(400, str(e))
        set_setting(f"oc:{rid}", json.dumps(prof))
        x("INSERT INTO commands(rig_id,type,params,status,created,by_user) VALUES(?,?,?,?,?,?)",
          (rid, "apply_oc", json.dumps({"profile": prof}), "pending", time.time(), user))
        audit(user, client_ip(req), "overclock", f"{r['name']}: {json.dumps(prof)}")
        return {"ok": True, "profile": prof}

    @app.get("/api/rigs/{rid}/log")
    def rig_log(rid: int, user=Depends(need_user)):
        cmds = q("SELECT * FROM commands WHERE rig_id=? ORDER BY id DESC LIMIT 25", (rid,))
        evs = q("SELECT * FROM events WHERE rig_id=? ORDER BY id DESC LIMIT 50", (rid,))
        return {"commands": [dict(c) for c in cmds], "events": [dict(e) for e in evs]}

    @app.post("/api/rigs/{rid}/command")
    async def command(rid: int, req: Request, user=Depends(need_user)):
        r = q("SELECT name FROM rigs WHERE id=?", (rid,), one=True)
        if not r:
            raise HTTPException(404)
        body = await req.json()
        ctype = body.get("type")
        params = body.get("params") or {}
        if ctype not in COMMANDS:
            raise HTTPException(400, "unknown command")
        clean = {}
        if ctype in ("set_power_limit", "lock_core", "reset_clocks"):
            g = params.get("gpu", "all")
            if g != "all":
                try:
                    g = int(g)
                    assert 0 <= g < 32
                except Exception:
                    raise HTTPException(400, "bad gpu index")
            clean["gpu"] = g
        if ctype == "set_power_limit":
            w = int(params.get("watts", 0))
            if not 50 <= w <= 600:
                raise HTTPException(400, "watts must be 50-600")
            clean["watts"] = w
        if ctype == "lock_core":
            mhz = int(params.get("mhz", 0))
            if not 300 <= mhz <= 3200:
                raise HTTPException(400, "MHz must be 300-3200")
            clean["mhz"] = mhz
        cid = x("INSERT INTO commands(rig_id,type,params,status,created,by_user) VALUES(?,?,?,?,?,?)",
                (rid, ctype, json.dumps(clean), "pending", time.time(), user))
        audit(user, client_ip(req), "command", f"{r['name']}: {ctype} {json.dumps(clean)}")
        return {"id": cid}

    @app.get("/api/audit")
    def audit_log(user=Depends(need_user)):
        return {"audit": [dict(a) for a in q("SELECT * FROM audit ORDER BY id DESC LIMIT 100")]}

    # ---- updates + farm key
    @app.get("/api/updates")
    def updates(user=Depends(need_user)):
        rigs = [{"id": r["id"], "name": r["name"],
                 "version": (json.loads(r["last_report"]) if r["last_report"] else {}).get("agent_version"),
                 "online": bool(r["last_seen"]) and time.time() - r["last_seen"] < OFFLINE_AFTER}
                for r in q("SELECT * FROM rigs ORDER BY name")]
        return {"server_version": VERSION, "latest": UPD["latest"], "error": UPD["error"],
                "last_check": UPD["last_check"], "checking": UPD["checking"], "notes": UPD["notes"],
                "repo": setting("update_repo"), "signed": bool(signing_pub()), "rigs": rigs}

    @app.post("/api/updates/check")
    def updates_check(req: Request, user=Depends(need_user)):
        threading.Thread(target=check_updates, daemon=True).start()
        return {"ok": True}

    @app.post("/api/updates/server")
    def updates_server(req: Request, user=Depends(need_user)):
        try:
            ver = install_server_update()
        except ValueError as e:
            raise HTTPException(400, str(e))
        audit(user, client_ip(req), "update_server", f"{VERSION} -> {ver}")
        return {"ok": True, "version": ver}

    @app.post("/api/updates/rigs")
    def updates_rigs(req: Request, user=Depends(need_user)):
        latest = UPD["latest"]
        if not latest:
            raise HTTPException(400, "No release downloaded yet")
        n = 0
        for r in q("SELECT * FROM rigs"):
            rep_ = json.loads(r["last_report"]) if r["last_report"] else {}
            if rep_ and vtuple(rep_.get("agent_version")) < vtuple(latest):
                x("INSERT INTO commands(rig_id,type,params,status,created,by_user) VALUES(?,?,?,?,?,?)",
                  (r["id"], "update_agent", "{}", "pending", time.time(), user))
                n += 1
        audit(user, client_ip(req), "update_rigs", f"{n} rigs -> {latest}")
        return {"queued": n}

    @app.patch("/api/rigs/{rid}")
    async def rename_rig(rid: int, req: Request, user=Depends(need_user)):
        name = str((await req.json()).get("name", "")).strip()
        if not name or len(name) > 40 or not all(c.isalnum() or c in "-_" for c in name):
            raise HTTPException(400, "Name: letters, numbers, - and _ only (max 40)")
        r = q("SELECT name FROM rigs WHERE id=?", (rid,), one=True)
        if not r:
            raise HTTPException(404)
        if q("SELECT 1 FROM rigs WHERE name=? AND id!=?", (name, rid), one=True):
            raise HTTPException(400, "Another rig already has that name")
        x("UPDATE rigs SET name=? WHERE id=?", (name, rid))
        audit(user, client_ip(req), "rename_rig", f"{r['name']} -> {name}")
        return {"ok": True}

    @app.get("/api/home")
    def home_get(user=Depends(need_user)):
        return {"auto_add": home_on(), "public_url": setting("public_url") or "",
                "tailscale_key_set": bool(setting("ts_authkey")), "listening_lan": app.state.lan}

    @app.post("/api/home")
    async def home_set(req: Request, user=Depends(need_user)):
        b = await req.json()
        if "auto_add" in b:
            set_setting("home_auto_add", "on" if b["auto_add"] else "off")
        if "public_url" in b:
            set_setting("public_url", str(b["public_url"]).strip().rstrip("/") or None)
        if b.get("tailscale_authkey"):
            set_setting("ts_authkey", str(b["tailscale_authkey"]).strip())
        if b.get("clear_tailscale_key"):
            set_setting("ts_authkey", None)
        audit(user, client_ip(req), "home_settings", json.dumps({k: v for k, v in b.items() if k != "tailscale_authkey"}))
        return home_get(user)

    @app.get("/api/farm")
    def farm(user=Depends(need_user)):
        return {"farm_key": farm_key()}

    @app.post("/api/farm/rotate")
    def farm_rotate(req: Request, user=Depends(need_user)):
        set_setting("farm_key", None)
        audit(user, client_ip(req), "rotate_farm_key")
        return {"farm_key": farm_key()}

    # ---- flight sheets
    def fs_rows():
        users = {}
        for r in q("SELECT id,name FROM rigs"):
            v = setting(f"fs:{r['id']}")
            if v:
                users.setdefault(int(v), []).append(r["name"])
        return [dict(f, rigs=users.get(f["id"], [])) for f in q("SELECT * FROM flightsheets ORDER BY name")]

    @app.get("/api/flightsheets")
    def fs_list(user=Depends(need_user)):
        return {"flightsheets": fs_rows(),
                "miners": {k: {"label": v["label"], "algo": v["algo"]} for k, v in MINERS.items()}}

    def push_fs(rid, fs, user):
        cmd, api_url = fs_command(fs)
        set_setting(f"fs:{rid}", fs["id"])
        x("INSERT INTO commands(rig_id,type,params,status,created,by_user) VALUES(?,?,?,?,?,?)",
          (rid, "apply_flightsheet", json.dumps({"name": fs["name"], "coin": fs["coin"], "miner_cmd": cmd,
                                                  "miner_api": api_url}), "pending", time.time(), user))

    @app.post("/api/flightsheets")
    async def fs_create(req: Request, user=Depends(need_user)):
        try:
            fs = clean_fs(await req.json())
        except ValueError as e:
            raise HTTPException(400, str(e))
        if q("SELECT 1 FROM flightsheets WHERE name=?", (fs["name"],), one=True):
            raise HTTPException(400, "A flight sheet with that name already exists")
        fid = x("INSERT INTO flightsheets(name,coin,miner,pool,login,algo,extra,custom,updated) VALUES(?,?,?,?,?,?,?,?,?)",
                (*[fs[k] for k in ("name", "coin", "miner", "pool", "login", "algo", "extra", "custom")], time.time()))
        audit(user, client_ip(req), "flightsheet_create", fs["name"])
        return {"id": fid}

    @app.put("/api/flightsheets/{fid}")
    async def fs_update(fid: int, req: Request, user=Depends(need_user)):
        if not q("SELECT 1 FROM flightsheets WHERE id=?", (fid,), one=True):
            raise HTTPException(404)
        try:
            fs = clean_fs(await req.json())
        except ValueError as e:
            raise HTTPException(400, str(e))
        if q("SELECT 1 FROM flightsheets WHERE name=? AND id!=?", (fs["name"], fid), one=True):
            raise HTTPException(400, "A flight sheet with that name already exists")
        x("UPDATE flightsheets SET name=?,coin=?,miner=?,pool=?,login=?,algo=?,extra=?,custom=?,updated=? WHERE id=?",
          (*[fs[k] for k in ("name", "coin", "miner", "pool", "login", "algo", "extra", "custom")], time.time(), fid))
        # rigs running this sheet get the new version right away
        fs["id"] = fid
        n = 0
        for r in q("SELECT id FROM rigs"):
            if setting(f"fs:{r['id']}") == str(fid):
                push_fs(r["id"], fs, user)
                n += 1
        audit(user, client_ip(req), "flightsheet_update", f"{fs['name']} (re-applied to {n} rigs)")
        return {"ok": True, "reapplied": n}

    @app.delete("/api/flightsheets/{fid}")
    def fs_delete(fid: int, req: Request, user=Depends(need_user)):
        f = q("SELECT name FROM flightsheets WHERE id=?", (fid,), one=True)
        if not f:
            raise HTTPException(404)
        x("DELETE FROM flightsheets WHERE id=?", (fid,))
        x("DELETE FROM settings WHERE key LIKE 'fs:%' AND value=?", (str(fid),))
        audit(user, client_ip(req), "flightsheet_delete", f["name"])
        return {"ok": True}

    @app.post("/api/flightsheets/{fid}/apply")
    async def fs_apply(fid: int, req: Request, user=Depends(need_user)):
        f = q("SELECT * FROM flightsheets WHERE id=?", (fid,), one=True)
        if not f:
            raise HTTPException(404)
        ids = [int(i) for i in (await req.json()).get("rig_ids", [])]
        names = []
        for rid in ids:
            r = q("SELECT name FROM rigs WHERE id=?", (rid,), one=True)
            if r:
                push_fs(rid, dict(f), user)
                names.append(r["name"])
        audit(user, client_ip(req), "flightsheet_apply", f"{f['name']} -> {', '.join(names)}")
        return {"applied": names}

    @app.get("/api/rigs/{rid}/flightsheet")
    def rig_fs(rid: int, user=Depends(need_user)):
        v = setting(f"fs:{rid}")
        return {"flightsheet_id": int(v) if v else None}

    # ---- autofan
    @app.get("/api/rigs/{rid}/autofan")
    def get_af(rid: int, user=Depends(need_user)):
        return {"autofan": json.loads(setting(f"af:{rid}") or '{"enabled": false, "target": 65, "min_fan": 40, "max_fan": 100}')}

    @app.put("/api/rigs/{rid}/autofan")
    async def put_af(rid: int, req: Request, user=Depends(need_user)):
        r = q("SELECT name FROM rigs WHERE id=?", (rid,), one=True)
        if not r:
            raise HTTPException(404)
        b = await req.json()
        try:
            af = {"enabled": bool(b.get("enabled")), "target": int(b.get("target", 65)),
                  "min_fan": int(b.get("min_fan", 40)), "max_fan": int(b.get("max_fan", 100))}
        except (TypeError, ValueError):
            raise HTTPException(400, "numbers only")
        if not (40 <= af["target"] <= 85 and 0 <= af["min_fan"] <= af["max_fan"] <= 100):
            raise HTTPException(400, "Target 40-85°C, and min fan ≤ max fan (0-100%)")
        set_setting(f"af:{rid}", json.dumps(af))
        x("INSERT INTO commands(rig_id,type,params,status,created,by_user) VALUES(?,?,?,?,?,?)",
          (rid, "set_autofan", json.dumps(af), "pending", time.time(), user))
        audit(user, client_ip(req), "autofan", f"{r['name']}: {json.dumps(af)}")
        return {"autofan": af}

    # ---- web shell (like Hive Shell). Opening one needs a fresh 2FA code.
    def own_shell(sid, user):
        sh = SHELLS.get(sid)
        if not sh or sh["user"] != user:
            raise HTTPException(404, "shell not found")
        return sh

    @app.post("/api/rigs/{rid}/shell")
    async def shell_open(rid: int, req: Request, user=Depends(need_user)):
        r = q("SELECT name FROM rigs WHERE id=?", (rid,), one=True)
        if not r:
            raise HTTPException(404)
        ip = "shell:" + client_ip(req)
        f = q("SELECT count,first FROM login_fails WHERE ip=?", (ip,), one=True)
        if f and f["count"] >= LOCKOUT_FAILS and time.time() - f["first"] < LOCKOUT_SECS:
            raise HTTPException(429, "Too many wrong codes. Try again in 15 minutes.")
        u = q("SELECT totp_secret FROM users WHERE username=?", (user,), one=True)
        if not u or not totp_ok(u["totp_secret"], str((await req.json()).get("code", ""))):
            if f and time.time() - f["first"] < LOCKOUT_SECS:
                x("UPDATE login_fails SET count=count+1 WHERE ip=?", (ip,))
            else:
                x("INSERT OR REPLACE INTO login_fails(ip,count,first) VALUES(?,?,?)", (ip, 1, time.time()))
            audit(user, client_ip(req), "shell_denied", r["name"])
            raise HTTPException(401, "Wrong 2FA code")
        now = time.time()
        for k in [k for k, v in SHELLS.items() if now - v["last"] > SHELL_IDLE or v["closed"] and now - v["last"] > 60]:
            SHELLS.pop(k, None)
        sid = secrets.token_urlsafe(18)
        SHELLS[sid] = {"rig": rid, "user": user, "out": [], "in": [], "last": now, "closed": False, "agent": False}
        x("INSERT INTO commands(rig_id,type,params,status,created,by_user) VALUES(?,?,?,?,?,?)",
          (rid, "open_shell", json.dumps({"sid": sid}), "pending", now, user))
        audit(user, client_ip(req), "shell_open", r["name"])
        return {"sid": sid}

    @app.get("/api/shell/{sid}/stream")
    async def shell_stream(sid: str, req: Request, user=Depends(need_user)):
        sh = own_shell(sid, user)

        async def gen():
            last_ping = time.time()
            yield "retry: 3000\n\n"
            while True:
                if await req.is_disconnected():
                    break
                if sh["out"]:
                    chunks, sh["out"] = sh["out"], []
                    for c in chunks:
                        yield "data: " + json.dumps({"d": c}) + "\n\n"
                    last_ping = time.time()
                if sh["closed"]:
                    yield "event: closed\ndata: {}\n\n"
                    break
                if time.time() - last_ping > 15:
                    yield ": ping\n\n"
                    last_ping = time.time()
                if time.time() - sh["last"] > SHELL_IDLE:
                    sh["closed"] = True
                await asyncio.sleep(0.03)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.post("/api/shell/{sid}/input")
    async def shell_input(sid: str, req: Request, user=Depends(need_user)):
        sh = own_shell(sid, user)
        b = await req.json()
        msg = {}
        if b.get("d"):
            msg["d"] = str(b["d"])[:65536]
        if b.get("cols") and b.get("rows"):
            msg["cols"], msg["rows"] = int(b["cols"]), int(b["rows"])
        if msg:
            sh["in"].append(msg)
        sh["last"] = time.time()
        return {"ok": True, "agent": sh["agent"]}

    @app.post("/api/shell/{sid}/close")
    def shell_close(sid: str, user=Depends(need_user)):
        sh = own_shell(sid, user)
        sh["closed"] = True
        return {"ok": True}

    # ---- agent api
    @app.get("/api/agent/wait")
    async def agent_wait(rig=Depends(need_rig)):
        """Long-poll: returns as soon as a command is waiting, so the dashboard feels instant."""
        end = time.time() + 25
        while time.time() < end:
            if q("SELECT 1 FROM commands WHERE rig_id=? AND status='pending' LIMIT 1", (rig["id"],), one=True):
                return {"wake": True}
            await asyncio.sleep(0.4)
        return {"wake": False}

    def rig_shell(sid, rig):
        sh = SHELLS.get(sid)
        if not sh or sh["rig"] != rig["id"]:
            raise HTTPException(404)
        sh["agent"] = True
        return sh

    @app.get("/api/agent/shell/{sid}/in")
    async def agent_shell_in(sid: str, rig=Depends(need_rig)):
        sh = rig_shell(sid, rig)
        end = time.time() + 20
        while time.time() < end and not sh["in"] and not sh["closed"]:
            await asyncio.sleep(0.02)
        msgs, sh["in"] = sh["in"], []
        return {"msgs": msgs, "closed": sh["closed"]}

    @app.post("/api/agent/shell/{sid}/out")
    async def agent_shell_out(sid: str, req: Request, rig=Depends(need_rig)):
        sh = rig_shell(sid, rig)
        b = await req.json()
        if b.get("d"):
            sh["out"].append(str(b["d"]))
        if b.get("exited"):
            sh["closed"] = True
        sh["last"] = time.time()
        return {"closed": sh["closed"]}

    @app.post("/api/agent/register")
    async def register(req: Request):
        peer = req.client.host if req.client else ""
        ip = "reg:" + client_ip(req)
        lan_ok = home_on() and is_lan_peer(peer)          # plug-and-play on the home network
        f = q("SELECT count,first FROM login_fails WHERE ip=?", (ip,), one=True)
        if not lan_ok and f and f["count"] >= LOCKOUT_FAILS and time.time() - f["first"] < LOCKOUT_SECS:
            raise HTTPException(429, "too many attempts")
        body = await req.json()
        name = "".join(c for c in str(body.get("name", "")) if c.isalnum() or c in "-_")[:40]
        hw = "".join(c for c in str(body.get("hw_id", "")) if c.isalnum() or c in ":-")[:64]
        key_ok = bool(body.get("farm_key")) and hmac.compare_digest(str(body.get("farm_key", "")), farm_key())
        if not (key_ok or lan_ok):
            if f and time.time() - f["first"] < LOCKOUT_SECS:
                x("UPDATE login_fails SET count=count+1 WHERE ip=?", (ip,))
            else:
                x("INSERT OR REPLACE INTO login_fails(ip,count,first) VALUES(?,?,?)", (ip, 1, time.time()))
            audit("farm-key", ip[4:], "register_failed", name)
            raise HTTPException(401, "bad farm key")
        if not name:
            raise HTTPException(400, "bad rig name")
        tok = "smk_" + secrets.token_urlsafe(32)
        # same hardware coming back (re-flashed USB) gets its old rig, history and overclock back
        r = q("SELECT id,name,last_seen FROM rigs WHERE hw_id=? AND hw_id!=''", (hw,), one=True) if hw else None
        if not r:
            r = q("SELECT id,name,last_seen FROM rigs WHERE name=?", (name,), one=True)
            if r and r["last_seen"] and time.time() - r["last_seen"] < 300:
                raise HTTPException(409, f"'{name}' is online already - use another RIG_NAME")
        if r:
            x("UPDATE rigs SET token_hash=?, hw_id=COALESCE(NULLIF(?,''),hw_id) WHERE id=?", (sha(tok), hw, r["id"]))
            name = r["name"]
        else:
            x("INSERT INTO rigs(name,token_hash,created,hw_id) VALUES(?,?,?,?)", (name, sha(tok), time.time(), hw))
        audit("home-network" if lan_ok and not key_ok else "farm-key", ip[4:], "register_rig", name)
        urls = [u for u in (setting("public_url"),) if u]
        return {"token": tok, "name": name, "server_urls": urls,
                "tailscale_authkey": setting("ts_authkey") or ""}

    @app.get("/api/agent/update")
    def agent_update(file: str, rig=Depends(need_rig)):
        ver = UPD["latest"]
        if not ver:
            raise HTTPException(404)
        tar, sig = release_files(ver)
        f = {tar.name: tar, sig.name: sig}.get(file)
        if not f or not f.exists():
            raise HTTPException(404)
        return FileResponse(f)

    @app.post("/api/agent/report")
    async def report(req: Request, rig=Depends(need_rig)):
        body = await req.json()
        now = time.time()
        events = body.pop("events", []) or []
        x("UPDATE rigs SET last_seen=?, last_report=? WHERE id=?", (now, json.dumps(body)[:200000], rig["id"]))
        gpus = body.get("gpus", [])
        power = sum(g.get("power") or 0 for g in gpus)
        temps = [g.get("temp") for g in gpus if isinstance(g.get("temp"), (int, float))]
        x("INSERT INTO samples(rig_id,ts,hashrate,power,max_temp) VALUES(?,?,?,?,?)",
          (rig["id"], now, body.get("hashrate") or 0, power, max(temps) if temps else None))
        for e in events[:20]:
            x("INSERT INTO events(rig_id,ts,level,message) VALUES(?,?,?,?)",
              (rig["id"], e.get("ts", now), str(e.get("level", "info"))[:10], str(e.get("message", ""))[:500]))
        pend = q("SELECT id,type,params FROM commands WHERE rig_id=? AND status='pending' ORDER BY id",
                 (rig["id"],))
        for c in pend:
            x("UPDATE commands SET status='sent' WHERE id=?", (c["id"],))
        latest = UPD["latest"] if UPD["latest"] and release_files(UPD["latest"])[0].exists() else None
        return {"commands": [{"id": c["id"], "type": c["type"], "params": json.loads(c["params"])} for c in pend],
                "server_time": now, "latest_version": latest, "name": rig["name"]}

    @app.post("/api/agent/result")
    async def result(req: Request, rig=Depends(need_rig)):
        body = await req.json()
        x("UPDATE commands SET status=?, finished=?, result=? WHERE id=? AND rig_id=?",
          ("done" if body.get("ok") else "failed", time.time(), str(body.get("output", ""))[:4000],
           int(body.get("id", 0)), rig["id"]))
        return {"ok": True}

    return app


def fmt_ago(s):
    s = int(s)
    if s < 120:
        return f"{s}s"
    if s < 7200:
        return f"{s // 60}m"
    if s < 172800:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def pruner():
    while True:
        try:
            x("DELETE FROM samples WHERE ts<?", (time.time() - HISTORY_DAYS * 86400,))
            x("DELETE FROM events WHERE ts<?", (time.time() - 30 * 86400,))
            x("DELETE FROM sessions WHERE expires<?", (time.time(),))
            # commands stuck as 'sent' for 10 min never got a result
            x("UPDATE commands SET status='lost' WHERE status='sent' AND created<?", (time.time() - 600,))
        except Exception as e:
            print("pruner:", e, file=sys.stderr)
        time.sleep(3600)

# --------------------------------------------------------------------------- cli


def cmd_setup(a):
    conn()
    if q("SELECT 1 FROM users", one=True) and not a.force:
        sys.exit("An admin already exists. Use 'setup --force' to replace it (old sessions are logged out).")
    username = a.username or input("Admin username: ").strip()
    pw = a.password
    if not pw:
        pw = getpass.getpass("Password (12+ chars): ")
        if pw != getpass.getpass("Repeat password: "):
            sys.exit("Passwords don't match.")
    if len(pw) < 12:
        sys.exit("Password must be at least 12 characters.")
    secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    uri = f"otpauth://totp/SmokeOS:{quote(username)}?secret={secret}&issuer=SmokeOS"
    print("\nAdd this to your authenticator app (Google Authenticator, Authy, 1Password, iPhone Passwords):")
    print(f"  Account: SmokeOS ({username})\n  Key:     {secret}\n")
    try:
        import qrcode
        img_path = DATA / "2fa-qr.png"
        DATA.mkdir(parents=True, exist_ok=True)
        qrcode.make(uri).save(img_path)
        print(f"  QR code saved to {img_path} - open it and scan it with your phone.\n")
        try:
            if os.name == "nt":
                os.startfile(str(img_path))          # opens the picture on Windows
        except Exception:
            pass
    except ImportError:
        print(f"  (no QR code: type the Key above into the app by hand)\n")
    if not a.password:
        for _ in range(3):
            if totp_ok(secret, input("Enter the 6-digit code from the app to confirm: ")):
                break
            print("That code didn't match, try again.")
        else:
            sys.exit("2FA not confirmed - nothing saved. Run setup again.")
    try:
        (DATA / "2fa-qr.png").unlink()
    except Exception:
        pass
    x("DELETE FROM users")
    x("DELETE FROM sessions")
    x("INSERT INTO users(username,pw_hash,totp_secret) VALUES(?,?,?)", (username, hash_pw(pw), secret))
    audit(username, "cli", "setup")
    print(f"\nAdmin '{username}' created. Start the server with:  python3 smokeos_server.py run")
    return secret


def cmd_add_rig(a):
    conn()
    if q("SELECT 1 FROM rigs WHERE name=?", (a.name,), one=True):
        sys.exit("A rig with that name already exists.")
    tok = "smk_" + secrets.token_urlsafe(32)
    x("INSERT INTO rigs(name,token_hash,created) VALUES(?,?,?)", (a.name, sha(tok), time.time()))
    audit("cli", "cli", "add_rig", a.name)
    print(f"Rig '{a.name}' created. Its token (shown once, keep it secret):\n\n  {tok}\n")


def cmd_updates(a):
    conn()
    if a.repo:
        set_setting("update_repo", a.repo.strip().strip("/"))
    if a.github_token:
        set_setting("github_token", a.github_token.strip())
    if a.clear_token:
        set_setting("github_token", None)
    print(f"repo:         {setting('update_repo') or '(not set)'}")
    print(f"github token: {'set' if setting('github_token') else 'none (public repo)'}")
    print(f"signing.pub:  {'found' if signing_pub() else 'MISSING - run tools/keygen.py'}")
    print(f"this version: {VERSION}")
    if a.check:
        check_updates()
        print(f"latest:       {UPD['latest'] or '-'}   {UPD['error'] or ''}")


def cmd_home(a):
    conn()
    if a.auto_add:
        set_setting("home_auto_add", a.auto_add)
    if a.public_url is not None:
        set_setting("public_url", a.public_url.strip().rstrip("/") or None)
    if a.tailscale_key:
        set_setting("ts_authkey", a.tailscale_key.strip())
    print(f"home plug-and-play: {'ON' if home_on() else 'off'}")
    print(f"dashboard link for rigs away from home: {setting('public_url') or '(not set)'}")
    print(f"tailscale auth key handed to new rigs: {'set' if setting('ts_authkey') else '(not set)'}")
    if home_on():
        print("Restart the server so it listens on your home network (it binds 0.0.0.0).")


def cmd_run(a):
    import uvicorn
    conn()
    if not q("SELECT 1 FROM users", one=True):
        sys.exit("No admin yet. Run:  python3 smokeos_server.py setup")
    host = a.host or ("0.0.0.0" if home_on() else "127.0.0.1")
    threading.Thread(target=discovery_responder, args=(a.port,), daemon=True).start()
    threading.Thread(target=pruner, daemon=True).start()
    threading.Thread(target=update_checker, daemon=True).start()
    farm_key()
    print(f"SmokeOS {VERSION} on http://{host}:{a.port}" + ("  (home plug-and-play ON)" if home_on() else ""))
    app = build_app()
    app.state.lan = host != "127.0.0.1"
    uvicorn.run(app, host=host, port=a.port, proxy_headers=True,
                forwarded_allow_ips="127.0.0.1", log_level="warning")


def main():
    p = argparse.ArgumentParser(description="SmokeOS server")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup")
    s.add_argument("--force", action="store_true")
    s.add_argument("--username")
    s.add_argument("--password", help=argparse.SUPPRESS)  # for automated tests only
    s.set_defaults(fn=cmd_setup)
    s = sub.add_parser("add-rig")
    s.add_argument("name")
    s.set_defaults(fn=cmd_add_rig)
    s = sub.add_parser("updates", help="configure GitHub auto-updates")
    s.add_argument("--repo", help="OWNER/REPO on GitHub")
    s.add_argument("--github-token", help="read-only token, only needed for a private repo")
    s.add_argument("--clear-token", action="store_true")
    s.add_argument("--check", action="store_true", help="check GitHub now")
    s.set_defaults(fn=cmd_updates)
    s = sub.add_parser("home", help="plug-and-play rigs on your home network")
    s.add_argument("--auto-add", choices=["on", "off"])
    s.add_argument("--public-url", help="your Tailscale dashboard link, e.g. https://box.tail1234.ts.net")
    s.add_argument("--tailscale-key", help="reusable Tailscale auth key handed to new rigs")
    s.set_defaults(fn=cmd_home)
    s = sub.add_parser("run")
    s.add_argument("--host", default=None, help="default: 0.0.0.0 when home plug-and-play is on, else 127.0.0.1")
    s.add_argument("--port", type=int, default=8420)
    s.set_defaults(fn=cmd_run)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
