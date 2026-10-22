#!/usr/bin/env python3
"""
SmokeOS rig agent. Python 3 standard library only, runs as root (systemd).

- Reports GPU stats (NVIDIA via nvidia-smi, AMD via rocm-smi) + hashrate every few seconds
- Optionally runs and babysits the miner (auto-restart, temp guard)
- Executes commands from the dashboard (restart miner, reboot, power limit, core lock)
- Registers itself with the farm key, and installs signed SmokeOS updates

Config: /etc/smokeos/agent.json  (see agent.example.json)
"""
import collections
import ctypes
import json
import os
import re
import shlex
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..", "common")]
import ed25519  # noqa: E402
import smokeos_rigconf as rigconf  # noqa: E402

DISCOVERY_PORT = 8429


def broadcast_addrs():
    addrs = {"255.255.255.255"}
    rc, out = run(["ip", "-4", "-o", "addr", "show"])
    for m in re.finditer(r"\bbrd (\d+\.\d+\.\d+\.\d+)", out if rc == 0 else ""):
        addrs.add(m.group(1))
    return addrs


def discover(timeout=3):
    """Find a SmokeOS server on the home network (UDP broadcast)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(timeout)
    try:
        for a in broadcast_addrs():
            try:
                s.sendto(b"SMOKEOS_DISCOVER v1", (a, DISCOVERY_PORT))
            except OSError:
                pass
        data, (ip, _) = s.recvfrom(512)
        info = json.loads(data.decode())
        if info.get("smokeos"):
            return f"http://{ip}:{int(info.get('port', 8420))}"
    except Exception:
        return None
    finally:
        s.close()


def _read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return default


VERSION = _read(os.path.join(HERE, "VERSION")) or _read(os.path.join(HERE, "..", "VERSION")) or "0.0.0"
CONFIG_PATH = os.environ.get("SMOKEOS_CONFIG", "/etc/smokeos/agent.json")
OC_PATH = os.path.join(os.path.dirname(CONFIG_PATH), "oc.json")      # per-GPU overclock, survives reboots
FS_PATH = os.path.join(os.path.dirname(CONFIG_PATH), "flightsheet.json")  # dashboard flight sheet (wins over rig.conf)
AF_PATH = os.path.join(os.path.dirname(CONFIG_PATH), "autofan.json")
PENDING = os.path.join(HERE, ".pending_update")
BACKUP = os.path.join(HERE, ".backup")
# files the agent owns: (path inside release archive, install path)
AGENT_FILES = [("agent/smokeos_agent.py", os.path.join(HERE, "smokeos_agent.py")),
               ("agent/smokeos_rigconf.py", os.path.join(HERE, "smokeos_rigconf.py")),
               ("common/ed25519.py", os.path.join(HERE, "ed25519.py")),
               ("VERSION", os.path.join(HERE, "VERSION")),
               ("signing.pub", os.path.join(HERE, "signing.pub")),
               ("agent/smokeos-agent.service", "/etc/systemd/system/smokeos-agent.service")]
if os.path.exists("/etc/smokeos-release"):        # running from the SmokeOS USB image
    AGENT_FILES += [("image/rootfs/usr/local/sbin/smokeos-boot", "/usr/local/sbin/smokeos-boot"),
                    ("image/rootfs/etc/systemd/system/smokeos-boot.service",
                     "/etc/systemd/system/smokeos-boot.service")]


def vtuple(v):
    try:
        return tuple(int(x) for x in str(v).lstrip("v").split("-")[0].split("."))
    except Exception:
        return (0,)
HIVE_KHS = "/run/hive/khs"          # HiveOS writes total kH/s here (monitor-only mode)

DEFAULTS = {
    "server_url": "",
    "token": "",
    "interval": 15,
    "manage_miner": True,
    "miner_cmd": "",
    "miner_api": "",
    "temp_limit": 83,
    "cool_temp": 65,
    "restart_delay": 10,
    "power_limits": {},               # {"0": 170, "1": 170} applied at startup
    "core_locks": {},                 # {"0": 1650} applied at startup
    "rig_name": "",                   # with farm_key: register automatically (no token needed)
    "farm_key": "",
    "auto_update": True,
}

UNIT = {"": 1, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18}
HR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([kKMGTPE]?)(?:H|h|Sol|sol)/s")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            cfg = dict(DEFAULTS, **json.load(f))
    except (FileNotFoundError, ValueError) as e:
        log(f"waiting for a valid {CONFIG_PATH} (fill in rig.conf on the SMOKEOS drive): {e}")
        time.sleep(60)
        sys.exit(1)
    cfg["server_url"] = (cfg["server_url"] or "").rstrip("/")
    if not cfg["rig_name"]:
        cfg["rig_name"] = rigconf.default_name()
    cfg["flightsheet"] = None
    try:
        fs = json.load(open(FS_PATH))
        if fs.get("miner_cmd"):
            cfg.update(miner_cmd=fs["miner_cmd"], miner_api=fs.get("miner_api", ""))
            cfg["flightsheet"] = {"name": fs.get("name"), "coin": fs.get("coin")}
    except Exception:
        pass
    return cfg


def save_config(**fields):
    with open(CONFIG_PATH) as f:
        data = json.load(f)
    data.update(fields)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, CONFIG_PATH)


def run(cmd, timeout=20):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as e:
        return 1, str(e)


def num(v):
    try:
        v = str(v).strip().split(" ")[0]
        return float(v) if v and "N/A" not in v else None
    except Exception:
        return None

# ------------------------------------------------------------------------- GPUs


def nvidia_gpus():
    if not shutil.which("nvidia-smi"):
        return []
    rc, out = run(["nvidia-smi", "--query-gpu=index,name,pci.bus_id,temperature.gpu,fan.speed,"
                   "power.draw,power.limit,clocks.sm,clocks.mem,utilization.gpu,power.default_limit",
                   "--format=csv,noheader,nounits"])
    gpus = []
    if rc != 0:
        return gpus
    for line in out.splitlines():
        p = [s.strip() for s in line.split(",")]
        if len(p) < 10:
            continue
        gpus.append({"vendor": "nvidia", "index": int(p[0]), "name": p[1].replace("NVIDIA ", ""),
                     "bus": p[2][-7:], "temp": num(p[3]), "fan": num(p[4]), "power": num(p[5]),
                     "power_limit": num(p[6]), "core": num(p[7]), "mem": num(p[8]), "util": num(p[9]),
                     "power_default": num(p[10]) if len(p) > 10 else None})
    return gpus


_SYS = {"t": 0, "v": {}}


def sysinfo():
    """Hive-style rig details (refreshed once a minute)."""
    if time.time() - _SYS["t"] < 60:
        return _SYS["v"]
    v = {}
    try:
        v["kernel"] = os.uname().release
        v["load"] = [round(x, 2) for x in os.getloadavg()]
    except Exception:
        pass
    try:
        cpus = [l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo") if l.startswith("model name")]
        if cpus:
            v["cpu"] = f"{len(cpus)} × {cpus[0]}"
    except Exception:
        pass
    try:
        mi = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo") if len(l.split()) >= 2}
        v["ram_total_mb"], v["ram_free_mb"] = mi.get("MemTotal", 0) // 1024, mi.get("MemAvailable", 0) // 1024
    except Exception:
        pass
    try:
        du = shutil.disk_usage("/")
        v["disk_free_gb"], v["disk_total_gb"] = round(du.free / 1e9, 1), round(du.total / 1e9, 1)
    except Exception:
        pass
    try:
        temps = []
        for z in os.listdir("/sys/class/thermal"):
            if z.startswith("thermal_zone"):
                temps.append(int(open(f"/sys/class/thermal/{z}/temp").read()) / 1000)
        if temps:
            v["cpu_temp"] = round(max(temps))
    except Exception:
        pass
    try:
        b = "/sys/class/dmi/id/"
        v["board"] = " ".join(open(b + f).read().strip() for f in ("board_vendor", "board_name") if os.path.exists(b + f))
    except Exception:
        pass
    try:
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        u.connect(("1.1.1.1", 53))
        v["local_ip"] = u.getsockname()[0]
        u.close()
    except Exception:
        pass
    v["mac"] = rigconf.hw_id()
    if shutil.which("nvidia-smi"):
        rc, out = run(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"])
        if rc == 0 and out and re.match(r"^\d+\.\d+", out.splitlines()[0].strip()):
            v["nvidia_driver"] = out.splitlines()[0].strip()
    try:
        v["os"] = "SmokeOS " + open("/etc/smokeos-release").read().strip()
    except Exception:
        v["os"] = "HiveOS + SmokeOS agent" if os.path.isdir("/hive") else "Linux + SmokeOS agent"
    _SYS["t"], _SYS["v"] = time.time(), v
    return v


class NVML:
    """Core/memory clock offsets and fan control through NVIDIA's own library
    (works headless, no X server). Offsets use the same units as HiveOS."""
    def __init__(self):
        self.lib = ctypes.CDLL("libnvidia-ml.so.1")
        self.lib.nvmlErrorString.restype = ctypes.c_char_p
        self._chk(self.lib.nvmlInit_v2(), "init")

    def _chk(self, rc, what):
        if rc != 0:
            raise RuntimeError(f"{what}: {self.lib.nvmlErrorString(rc).decode()}")

    def _h(self, idx):
        h = ctypes.c_void_p()
        self._chk(self.lib.nvmlDeviceGetHandleByIndex_v2(ctypes.c_uint(idx), ctypes.byref(h)), "handle")
        return h

    def core_offset(self, idx, mhz):
        fn = getattr(self.lib, "nvmlDeviceSetGpcClkVfOffset", None)
        if fn is None:
            raise RuntimeError("core offset not supported by this driver")
        self._chk(fn(self._h(idx), ctypes.c_int(mhz)), "core offset")

    def mem_offset(self, idx, mhz):
        fn = getattr(self.lib, "nvmlDeviceSetMemClkVfOffset", None)
        if fn is None:
            raise RuntimeError("memory offset not supported by this driver")
        self._chk(fn(self._h(idx), ctypes.c_int(mhz)), "mem offset")

    def fan(self, idx, pct):
        h = self._h(idx)
        n = ctypes.c_uint(0)
        self._chk(self.lib.nvmlDeviceGetNumFans(h, ctypes.byref(n)), "fan count")
        for f in range(n.value):
            if pct:
                self._chk(self.lib.nvmlDeviceSetFanSpeed_v2(h, ctypes.c_uint(f), ctypes.c_uint(pct)), "fan")
            else:
                self._chk(self.lib.nvmlDeviceSetDefaultFanSpeed_v2(h, ctypes.c_uint(f)), "fan auto")


def amd_gpus(start_index):
    if not shutil.which("rocm-smi"):
        return []
    rc, out = run(["rocm-smi", "--showtemp", "--showfan", "--showpower", "--showuse",
                   "--showproductname", "--showclocks", "--showbus", "--json"])
    if rc != 0:
        return []
    try:
        data = json.loads(out[out.index("{"):])
    except Exception:
        return []

    def pick(d, *needles):
        for k, v in d.items():
            kl = k.lower()
            if all(n in kl for n in needles):
                return v
        return None

    gpus = []
    for i, (card, d) in enumerate(sorted((k, v) for k, v in data.items() if k.startswith("card"))):
        sclk = re.search(r"(\d+)\s*Mhz", str(pick(d, "sclk") or ""), re.I)
        mclk = re.search(r"(\d+)\s*Mhz", str(pick(d, "mclk") or ""), re.I)
        bus = re.search(r"([0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.\d)$", str(pick(d, "bus") or ""))
        gpus.append({"vendor": "amd", "index": start_index + i, "amd_card": card,
                     "bus": bus.group(1) if bus else "",
                     "name": str(pick(d, "card series") or pick(d, "card model") or "AMD GPU"),
                     "temp": num(pick(d, "temperature", "edge") or pick(d, "temperature")),
                     "fan": num(pick(d, "fan speed (%)") or pick(d, "fan", "%")),
                     "power": num(pick(d, "power", "(w)")), "power_limit": None,
                     "core": num(sclk.group(1)) if sclk else None,
                     "mem": num(mclk.group(1)) if mclk else None,
                     "util": num(pick(d, "gpu use"))})
    return gpus


def all_gpus():
    n = nvidia_gpus()
    return n + amd_gpus(len(n))

# ------------------------------------------------------------------------ miner


def find_total_hashrate(obj):
    """Walk a miner API JSON blob and find a total hashrate (H/s)."""
    best = None
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if isinstance(v, (int, float)) and "hashrate" in kl and "total" in kl:
                return float(v)
            if kl == "total" and isinstance(v, (int, float)):
                best = float(v) if best is None else best
            if isinstance(v, (dict, list)):
                r = find_total_hashrate(v)
                if r is not None and "hashrate" in kl:
                    return r
                best = r if best is None else best
    elif isinstance(obj, list):
        for v in obj:
            r = find_total_hashrate(v)
            if r is not None:
                return r
    return best


HIVE_STATS = "/run/hive/miner_stats"        # HiveOS: per-GPU hashrate + bus numbers (monitor-only mode)


def bus_int(v):
    """'05:00.0' / '0000:05:00.0' / 5 -> 5"""
    if isinstance(v, (int, float)):
        return int(v)
    m = re.search(r"([0-9a-fA-F]{2}):[0-9a-fA-F]{2}\.\d", str(v))
    if m:
        return int(m.group(1), 16)
    return int(v) if str(v).isdigit() else None


def gpu_line_hashrates(line):
    """'GPU0: 84.1 TH/s GPU1: 83.5 TH/s' or 'GPU #0: P106-100 - 9.63 MH/s' -> {0: H/s, 1: H/s}"""
    out = {}
    for part in re.split(r"(?=\bGPU\s*#?\s*\d+)", line):
        m = re.match(r"GPU\s*#?\s*(\d+)", part)
        if not m:
            continue
        h = HR_RE.search(part[m.end():])
        if h:
            out[int(m.group(1))] = float(h.group(1)) * UNIT.get(h.group(2), 1)
    return out


def _hs_of(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        for k in ("now", "total", "current", "10s", "60s"):
            if isinstance(v.get(k), (int, float)):
                return float(v[k])
        for x in v.values():
            if isinstance(x, (int, float)):
                return float(x)
    return None


def api_gpu_hashrates(obj):
    """Find per-GPU hashrates in a miner's JSON stats. Returns ('bus', {bus: H/s}) when the
    miner says which PCI bus each GPU is on, ('idx', {n: H/s}) otherwise, or None."""
    found = []

    def walk(o):
        if isinstance(o, dict):
            keys = {str(k).lower(): k for k in o}
            gk = [k for k in o if re.fullmatch(r"gpu\d+", str(k).lower())]
            if gk:
                idx = {int(str(k)[3:]): _hs_of(o[k]) for k in gk}
                found.append(("idx", {i: v for i, v in idx.items() if v is not None}))
            for v in o.values():
                walk(v)
        elif isinstance(o, list) and o and all(isinstance(x, dict) for x in o):
            bus, idx = {}, {}
            for n, d in enumerate(o):
                lk = {str(k).lower(): k for k in d}
                hk = next((lk[k] for k in lk if "hashrate" in k or k in ("hs", "speed")), None)
                bk = next((lk[k] for k in lk if "bus" in k), None)
                if hk is None:
                    continue
                hs = _hs_of(d[hk])
                if hs is None:
                    continue
                b = bus_int(d[bk]) if bk is not None else None
                if b is not None:
                    bus[b] = hs
                idk = next((lk[k] for k in ("device_id", "gpu_id", "id") if k in lk), None)
                idx[int(d[idk]) if idk is not None and str(d[idk]).isdigit() else n] = hs
            if bus:
                found.append(("bus", bus))
            elif idx:
                found.append(("idx", idx))
            for x in o:
                walk(x)
    walk(obj)
    for kind in ("bus", "idx"):
        for k, v in found:
            if k == kind and v:
                return k, v
    return None


class Miner:
    def __init__(self, cfg, agent):
        self.cfg, self.agent = cfg, agent
        self.proc = None
        self.wanted = bool(cfg["manage_miner"] and cfg["miner_cmd"])
        self.started_at = None
        self.restarts = 0
        self.lines = collections.deque(maxlen=60)
        self.hr_total = None   # (value H/s, ts) from a line containing "total"
        self.hr_any = None
        self.gpu_hr = {}       # miner's GPU number -> (H/s, ts), from its console output
        self.lock = threading.Lock()

    def start(self):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                return
            name = self.cfg.get("display_name") or self.cfg.get("rig_name") or "rig"
            cmd = self.cfg["miner_cmd"].replace("%RIG_NAME%", name)
            self.used_name = name
            log("starting miner:", cmd)
            self.proc = subprocess.Popen(shlex.split(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, text=True, bufsize=1,
                                         start_new_session=True, errors="replace",
                                         cwd=os.path.dirname(shlex.split(cmd)[0]) or None)
            self.started_at = time.time()
            threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def stop(self):
        with self.lock:
            p = self.proc
            if not p or p.poll() is not None:
                return
            log("stopping miner")
            try:
                os.killpg(p.pid, signal.SIGTERM)
                p.wait(timeout=15)
            except Exception:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except Exception:
                    pass
            self.proc = None          # deliberate stop: don't count it as a crash
            self.started_at = None

    def running(self):
        return bool(self.proc and self.proc.poll() is None)

    def _read(self, proc):
        for raw in proc.stdout:
            line = ANSI_RE.sub("", raw).rstrip()
            if not line:
                continue
            self.lines.append(line)
            per = gpu_line_hashrates(line)
            if per:
                now = time.time()
                for i, v in per.items():
                    self.gpu_hr[i] = (v, now)
            m = HR_RE.findall(line)
            if m and not (per and "total" not in line.lower() and len(m) == len(per)):
                val = max(float(v) * UNIT.get(u, 1) for v, u in m)
                if "total" in line.lower():
                    self.hr_total = (val, time.time())
                self.hr_any = (val, time.time())

    def hashrate(self):
        api = self.cfg.get("miner_api")
        if api:
            try:
                with urllib.request.urlopen(api, timeout=3) as r:
                    v = find_total_hashrate(json.loads(r.read().decode()))
                    if v:
                        return v
            except Exception:
                pass
        if not self.cfg["manage_miner"] and os.path.exists(HIVE_KHS):
            try:
                return float(open(HIVE_KHS).read().strip()) * 1000
            except Exception:
                pass
        now = time.time()
        for hr in (self.hr_total, self.hr_any):
            if hr and now - hr[1] < 180:
                return hr[0]
        return 0.0

    def gpu_hashrates(self, gpus):
        """Hashrate for each of our GPUs (by our index). Matches by PCI bus when the miner
        reports it, otherwise assumes the miner numbers GPUs in PCI bus order."""
        res = None
        api = self.cfg.get("miner_api")
        if api:
            try:
                with urllib.request.urlopen(api, timeout=3) as r:
                    res = api_gpu_hashrates(json.loads(r.read().decode()))
            except Exception:
                res = None
        if res is None and not self.cfg["manage_miner"] and os.path.exists(HIVE_STATS):
            try:
                st = json.load(open(HIVE_STATS))
                mult = {"hs": 1, "khs": 1e3, "mhs": 1e6, "ghs": 1e9, "ths": 1e12}.get(str(st.get("hs_units", "khs")).lower(), 1e3)
                hs = [float(x) * mult for x in st.get("hs", []) if isinstance(x, (int, float, str)) and str(x).replace(".", "", 1).isdigit()]
                if st.get("bus_numbers") and len(st["bus_numbers"]) == len(hs):
                    res = ("bus", {int(b): v for b, v in zip(st["bus_numbers"], hs)})
                elif hs:
                    res = ("idx", dict(enumerate(hs)))
            except Exception:
                res = None
        if res is None:
            now = time.time()
            fresh = {i: v for i, (v, t) in self.gpu_hr.items() if now - t < 180}
            if fresh:
                res = ("idx", fresh)
        if not res:
            return {}
        kind, vals = res
        if kind == "bus":
            return {g["index"]: vals[bus_int(g.get("bus"))] for g in gpus if bus_int(g.get("bus")) in vals}
        cmd = (self.cfg.get("miner_cmd") or "").lower()
        pool = [g for g in gpus if g["vendor"] == "nvidia"] if "t-rex" in cmd else list(gpus)
        pool.sort(key=lambda g: (bus_int(g.get("bus")) is None, bus_int(g.get("bus")) or 0, g["index"]))
        return {g["index"]: vals[n] for n, g in enumerate(pool) if n in vals}

    def supervise(self):
        """Keep the miner alive unless stopped by the user or the temp guard."""
        while True:
            if self.wanted and not self.agent.cooldown and not self.running():
                if self.proc is not None:
                    self.restarts += 1
                    code = self.proc.poll()
                    self.agent.event("warn", f"Miner exited (code {code}), restarting")
                    time.sleep(self.cfg["restart_delay"])
                try:
                    self.start()
                except Exception as e:
                    self.agent.event("crit", f"Miner failed to start: {e}")
                    time.sleep(30)
            time.sleep(2)

# ------------------------------------------------------------------------ agent


class Agent:
    def __init__(self, cfg):
        self.cfg = cfg
        self.events = []
        self.cooldown = False
        self.miner = Miner(cfg, self)
        self.ctx = ssl.create_default_context()
        self.update_failed = {}
        self.restart_self = False
        self.good_url = ""
        self.last_discover = 0
        self.wake = threading.Event()
        self.supervising = False
        self.nvml = None
        self.af_set = {}
        self.af_err = set()
        self.last_gpus = []

    def event(self, level, message):
        log(f"[{level}] {message}")
        self.events.append({"ts": time.time(), "level": level, "message": message})
        self.events = self.events[-50:]

    # -- talking to the server: try every known address, then look on the home network
    def urls(self):
        out = []
        for u in [self.cfg.get("server_url")] + list(self.cfg.get("server_urls") or []) + [self.cfg.get("lan_url")]:
            u = (u or "").rstrip("/")
            if u and u not in out:
                out.append(u)
        if self.good_url in out:                      # last address that worked goes first
            out.remove(self.good_url)
            out.insert(0, self.good_url)
        return out

    def open(self, path, data=None, timeout=15, auth=True):
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = "Bearer " + self.cfg["token"]
        last = None
        for attempt in range(2):
            for base in self.urls():
                try:
                    req = urllib.request.Request(base + path, data=data, headers=headers)
                    r = urllib.request.urlopen(req, timeout=timeout, context=self.ctx)
                    if base != self.good_url:
                        self.good_url = base
                        log("connected to", base)
                    return r
                except urllib.error.HTTPError:
                    raise                               # the server answered: don't try elsewhere
                except Exception as e:
                    last = e
            if attempt == 0 and time.time() - self.last_discover > 30:
                self.last_discover = time.time()
                found = discover()
                if found:
                    log("found SmokeOS server on the home network:", found)
                    if found != self.cfg.get("lan_url"):
                        self.cfg["lan_url"] = found
                        save_config(lan_url=found)
                    continue
            break
        raise last or RuntimeError("no SmokeOS server found (set SERVER_URL, or turn on home plug-and-play)")

    def get_file(self, path, dest):
        with self.open(path, timeout=300) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)

    def register(self):
        """Farm key, or plug-and-play on the home network -> our own token + where to find the server."""
        body = json.dumps({"farm_key": self.cfg.get("farm_key", ""), "name": self.cfg["rig_name"],
                           "hw_id": rigconf.hw_id()}).encode()
        with self.open("/api/agent/register", data=body, auth=False) as r:
            resp = json.loads(r.read().decode())
        self.cfg["token"] = resp["token"]
        self.cfg["server_urls"] = resp.get("server_urls") or self.cfg.get("server_urls") or []
        fields = {"token": resp["token"], "server_urls": self.cfg["server_urls"]}
        if resp.get("tailscale_authkey"):
            fields["tailscale_authkey"] = resp["tailscale_authkey"]
        save_config(**fields)
        self.event("info", f"Registered with the dashboard as {resp.get('name', self.cfg['rig_name'])}")
        if resp.get("tailscale_authkey"):
            threading.Thread(target=self.join_tailscale, args=(resp["tailscale_authkey"],), daemon=True).start()

    def join_tailscale(self, key):
        """So the rig still reaches the dashboard after it moves away from home."""
        if not shutil.which("tailscale"):
            return
        if run(["tailscale", "status"])[0] == 0:
            return
        host = "".join(c for c in self.cfg["rig_name"] if c.isalnum() or c == "-") or "smokeos-rig"
        rc, out = run(["tailscale", "up", "--authkey", key, "--hostname", host, "--ssh"], timeout=90)
        self.event("info" if rc == 0 else "warn", "Joined Tailscale" if rc == 0 else f"Tailscale join failed: {out[-200:]}")

    # -- self update
    def update_to(self, ver):
        if self.update_failed.get(ver, 0) > time.time() - 6 * 3600:
            return False, f"{ver} failed recently, not retrying yet"
        pub = _read(os.path.join(HERE, "signing.pub"))
        if not pub:
            return False, "no signing.pub installed - updates disabled"
        name = f"smokeos-{ver}.tar.gz"
        tmp = tempfile.mkdtemp(prefix="smokeos-upd-")
        try:
            self.get_file(f"/api/agent/update?file={name}", os.path.join(tmp, name))
            self.get_file(f"/api/agent/update?file={name}.sig", os.path.join(tmp, name + ".sig"))
            if not ed25519.verify_file(pub, os.path.join(tmp, name), _read(os.path.join(tmp, name + ".sig"))):
                self.update_failed[ver] = time.time()
                self.event("crit", f"Update {ver} REJECTED: bad signature")
                return False, "bad signature"
            with tarfile.open(os.path.join(tmp, name)) as t:
                for m in t.getmembers():
                    if m.name.startswith("/") or ".." in m.name.split("/"):
                        raise ValueError("unsafe path in archive")
                if hasattr(tarfile, "data_filter"):
                    t.extractall(os.path.join(tmp, "x"), filter="data")
                else:
                    t.extractall(os.path.join(tmp, "x"))
            src = os.path.join(tmp, "x", "smokeos")
            # back up current files, then install the new ones
            shutil.rmtree(BACKUP, ignore_errors=True)
            os.makedirs(BACKUP)
            manifest = {}
            for rel, dest in AGENT_FILES:
                if os.path.exists(dest):
                    name = rel.replace("/", "__")
                    shutil.copy2(dest, os.path.join(BACKUP, name))
                    manifest[name] = dest
            with open(os.path.join(BACKUP, "manifest.json"), "w") as f:
                json.dump(manifest, f)
            for rel, dest in AGENT_FILES:
                s_ = os.path.join(src, rel)
                if os.path.exists(s_):
                    shutil.copy2(s_, dest + ".new")
                    os.replace(dest + ".new", dest)
            with open(PENDING, "w") as f:
                json.dump({"from": VERSION, "to": ver, "starts": 0}, f)
            run(["systemctl", "daemon-reload"])
        except Exception as e:
            self.update_failed[ver] = time.time()
            self.event("crit", f"Update to {ver} failed: {e}")
            return False, str(e)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.event("info", f"Updated files {VERSION} -> {ver}, restarting agent")
        self.restart_self = True
        return True, f"installed {ver}, restarting"

    def post(self, path, payload):
        with self.open(path, data=json.dumps(payload).encode()) as r:
            return json.loads(r.read().decode())

    def get_json(self, path, timeout=15):
        with self.open(path, timeout=timeout) as r:
            return json.loads(r.read().decode())

    # -- instant commands: a long-poll that wakes the main loop when the dashboard sends something
    def waker(self):
        while True:
            if not self.cfg.get("token"):
                time.sleep(5)
                continue
            try:
                if self.get_json("/api/agent/wait", timeout=35).get("wake"):
                    self.wake.set()
                    time.sleep(1)
            except Exception:
                time.sleep(10)

    # -- web shell (dashboard "Shell" button)
    def run_shell(self, sid):
        import base64
        import fcntl
        import pty
        import select
        import struct
        import termios
        pid, fd = pty.fork()
        if pid == 0:                                     # child: a login shell for root
            os.environ.update(TERM="xterm-256color", HOME="/root", LANG="C.UTF-8")
            try:
                os.chdir("/root")
            except OSError:
                os.chdir("/")
            sh = "/bin/bash" if os.path.exists("/bin/bash") else "/bin/sh"
            os.execvp(sh, [sh, "-l"])
        state = {"alive": True}

        def send(data=b"", exited=False):
            return self.post(f"/api/agent/shell/{sid}/out",
                             {"d": base64.b64encode(data).decode() if data else "", "exited": exited})

        def reader():
            buf, last = b"", time.time()
            while state["alive"]:
                r, _, _ = select.select([fd], [], [], 0.03)
                if r:
                    try:
                        chunk = os.read(fd, 65536)
                    except OSError:
                        chunk = b""
                    if not chunk:
                        break
                    buf += chunk
                if buf and (time.time() - last > 0.02 or len(buf) > 32768):
                    try:
                        if send(buf).get("closed"):
                            break
                    except Exception:
                        pass
                    buf, last = b"", time.time()
            try:
                send(buf, exited=True)
            except Exception:
                pass
            state["alive"] = False

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        self.event("info", "Shell opened from the dashboard")
        try:
            while state["alive"]:
                try:
                    resp = self.get_json(f"/api/agent/shell/{sid}/in", timeout=30)
                except urllib.error.HTTPError:
                    break                                 # session gone on the server
                except Exception:
                    time.sleep(1)
                    continue
                for m in resp.get("msgs", []):
                    if m.get("d"):
                        os.write(fd, base64.b64decode(m["d"]))
                    if m.get("cols") and m.get("rows"):
                        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", int(m["rows"]), int(m["cols"]), 0, 0))
                if resp.get("closed"):
                    break
        finally:
            state["alive"] = False
            try:
                os.kill(pid, signal.SIGHUP)
                os.waitpid(pid, 0)
            except Exception:
                pass
            try:
                os.close(fd)
            except OSError:
                pass
            self.event("info", "Shell closed")

    # -- flight sheet + autofan
    def apply_flightsheet(self, p):
        if not self.cfg["manage_miner"]:
            return False, "this rig is monitor-only (miner managed by HiveOS) - flight sheets are off"
        tmp = FS_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(p, f, indent=2)
        os.replace(tmp, FS_PATH)
        self.cfg.update(miner_cmd=p["miner_cmd"], miner_api=p.get("miner_api", ""))
        self.cfg["flightsheet"] = {"name": p.get("name"), "coin": p.get("coin")}
        self.miner.cfg = self.cfg
        self.miner.hr_total = self.miner.hr_any = None
        self.miner.wanted = True
        self.miner.stop()
        if not self.supervising:
            self.supervising = True
            threading.Thread(target=self.miner.supervise, daemon=True).start()
        return True, f"flight sheet '{p.get('name')}' applied, miner restarting:\n{p['miner_cmd']}"

    def autofan_tick(self, gpus):
        try:
            af = json.load(open(AF_PATH))
        except Exception:
            return
        try:
            oc = json.load(open(OC_PATH))
        except Exception:
            oc = {}
        try:
            nvml = self.nvml or NVML()
            self.nvml = nvml
        except Exception:
            return
        for g in gpus:
            if g["vendor"] != "nvidia" or g.get("temp") is None:
                continue
            if int((oc.get(str(g["index"])) or {}).get("fan", 0) or 0):
                continue                                   # a fixed fan % from the overclock tab wins
            key = g["index"]
            try:
                if af.get("enabled"):
                    lo, hi, tgt = af["min_fan"], af["max_fan"], af["target"]
                    want = lo + (g["temp"] - (tgt - 8)) * (hi - lo) / 12.0
                    want = int(max(lo, min(hi, want)))
                    if abs(want - self.af_set.get(key, -100)) >= 3:
                        nvml.fan(key, max(want, 1))
                        self.af_set[key] = want
                elif key in self.af_set:
                    nvml.fan(key, 0)                       # back to the card's own fan curve
                    del self.af_set[key]
            except Exception as e:
                if key not in self.af_err:
                    self.af_err.add(key)
                    self.event("warn", f"Autofan can't control GPU{key}: {e}")

    # -- commands
    def nv(self, gpu, *args):
        cmd = ["nvidia-smi"] + ([] if gpu == "all" else ["-i", str(gpu)]) + list(args)
        return run(cmd)

    def execute(self, c):
        t, p = c["type"], c.get("params", {})
        if t == "restart_miner":
            self.miner.wanted = True
            self.miner.stop()
            return True, "miner restarting"
        if t == "stop_miner":
            self.miner.wanted = False
            self.miner.stop()
            return True, "miner stopped"
        if t == "start_miner":
            if not self.cfg["miner_cmd"]:
                return False, "no miner_cmd configured on this rig"
            self.miner.wanted = True
            return True, "miner starting"
        if t == "update_agent":
            if not self.latest or vtuple(self.latest) <= vtuple(VERSION):
                return True, f"already up to date ({VERSION})"
            self.update_failed.pop(self.latest, None)
            return self.update_to(self.latest)
        if t == "reboot":
            threading.Timer(5, lambda: run(["systemctl", "reboot"])).start()
            return True, "rebooting in 5s"
        gpu = p.get("gpu", "all")
        if gpu != "all" and self.is_amd(gpu):
            return False, "power/clock control is NVIDIA-only in v1"
        if t == "apply_flightsheet":
            return self.apply_flightsheet(p)
        if t == "set_autofan":
            with open(AF_PATH, "w") as f:
                json.dump(p, f)
            return True, ("autofan on: target %d°C, fan %d-%d%%" % (p["target"], p["min_fan"], p["max_fan"])
                          if p.get("enabled") else "autofan off (cards back to their own fan curve)")
        if t == "open_shell":
            if sum(1 for th in threading.enumerate() if th.name.startswith("shell-")) >= 3:
                return False, "too many shells open on this rig"
            threading.Thread(target=self.run_shell, args=(p["sid"],), daemon=True, name="shell-" + p["sid"][:6]).start()
            return True, "shell started"
        if t == "apply_oc":
            prof = p.get("profile") or {}
            try:
                prev = json.load(open(OC_PATH))
            except Exception:
                prev = {}
            ok, out = self.apply_oc(prof, prev)
            try:
                tmp = OC_PATH + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(prof, f, indent=2)
                os.replace(tmp, OC_PATH)
            except Exception as e:
                out += f"\n(could not save {OC_PATH}: {e})"
            return ok, out
        if t == "set_power_limit":
            rc, out = self.nv(gpu, "-pl", str(p["watts"]))
            return rc == 0, out
        if t == "lock_core":
            rc, out = self.nv(gpu, "-lgc", f"{p['mhz']},{p['mhz']}")
            return rc == 0, out
        if t == "reset_clocks":
            rc, out = self.nv(gpu, "-rgc")
            return rc == 0, out
        return False, "unknown command"

    def is_amd(self, idx):
        return any(g["index"] == idx and g["vendor"] == "amd" for g in self.last_gpus)

    def apply_oc(self, profile, previous=None):
        """profile: {"0": {"core_lock":1650,"mem_offset":-500,"power_limit":170, ...}, ...}
        Only fields you actually set are touched. A field that was set before and is now
        empty/0 is put back to stock. Nothing is ever pre-loaded."""
        previous = previous or {}
        lines, all_ok = [], True
        nvml = None
        try:
            nvml = NVML()
        except Exception as e:
            nvml_err = str(e)
        gpus = {g["index"]: g for g in (self.last_gpus or all_gpus())}
        for idx_s, o in sorted(profile.items(), key=lambda kv: int(kv[0])):
            idx = int(idx_s)
            g = gpus.get(idx)
            if not g:
                lines.append(f"GPU{idx}: not found")
                all_ok = False
                continue
            if g["vendor"] != "nvidia":
                lines.append(f"GPU{idx}: AMD tuning not supported yet (monitor only)")
                continue
            res = []
            was = previous.get(idx_s, {}) or {}

            def want(k):
                """'set' = apply value, 'reset' = cleared since last time, None = leave alone"""
                if int(o.get(k, 0) or 0):
                    return "set"
                return "reset" if int(was.get(k, 0) or 0) else None

            def step(label, fn):
                nonlocal all_ok
                try:
                    r = fn()
                    if isinstance(r, tuple) and r[0] != 0:
                        raise RuntimeError(r[1].splitlines()[-1] if r[1] else "failed")
                    res.append(f"{label} ok")
                except Exception as e:
                    all_ok = False
                    res.append(f"{label} FAILED ({e})")

            if want("power_limit"):
                pl = int(o.get("power_limit", 0) or 0) or g.get("power_default")
                if pl:
                    step(f"PL {int(pl)}W" + ("" if o.get("power_limit") else " (stock)"),
                         lambda: self.nv(idx, "-pl", str(int(pl))))
            if want("core_lock"):
                cl = int(o.get("core_lock", 0) or 0)
                step(f"core lock {cl}" if cl else "core unlock (stock)",
                     lambda: self.nv(idx, "-lgc", f"{cl},{cl}") if cl else self.nv(idx, "-rgc"))
            for key, label, fn_name in (("core_offset", "core", "core_offset"), ("mem_offset", "mem", "mem_offset"),
                                        ("fan", "fan", "fan")):
                if not want(key):
                    continue
                v = int(o.get(key, 0) or 0)
                if nvml is None:
                    all_ok = False
                    res.append(f"{label} FAILED ({nvml_err})")
                    continue
                txt = (f"fan {v}%" if v else "fan auto") if key == "fan" else (f"{label} {v:+d}" if v else f"{label} offset stock")
                step(txt, lambda f=getattr(nvml, fn_name), v=v: f(idx, v))
            if not res:
                res.append("stock (nothing changed)")
            lines.append(f"GPU{idx} {g['name']}: " + ", ".join(res))
        return all_ok, "\n".join(lines) or "nothing to apply"

    def apply_startup_tuning(self):
        for idx, w in (self.cfg.get("power_limits") or {}).items():
            rc, out = self.nv(idx, "-pl", str(w))
            log(f"GPU{idx} power limit {w}W:", "ok" if rc == 0 else out)
        for idx, mhz in (self.cfg.get("core_locks") or {}).items():
            rc, out = self.nv(idx, "-lgc", f"{mhz},{mhz}")
            log(f"GPU{idx} core lock {mhz}:", "ok" if rc == 0 else out)
        # the dashboard's per-GPU overclock (oc.json) wins over rig.conf values
        if os.path.exists(OC_PATH):
            try:
                ok, out = self.apply_oc(json.load(open(OC_PATH)))
                self.event("info" if ok else "warn", "Overclock applied at start" + ("" if ok else ":\n" + out))
            except Exception as e:
                self.event("warn", f"Could not apply saved overclock: {e}")

    # -- temp guard
    def temp_guard(self, gpus):
        temps = [g["temp"] for g in gpus if g.get("temp") is not None]
        if not temps:
            return
        hot = max(temps)
        if not self.cooldown and hot >= self.cfg["temp_limit"]:
            self.cooldown = True
            self.event("crit", f"Temp guard: {hot:.0f}°C >= {self.cfg['temp_limit']}°C, pausing miner")
            self.miner.stop()
        elif self.cooldown and hot < self.cfg["cool_temp"]:
            self.cooldown = False
            self.event("info", f"Temp guard: cooled to {hot:.0f}°C, resuming")

    def check_pending(self):
        """smokeos_guard.py does the rollback; here we just report it."""
        rb = os.path.join(HERE, ".rolled_back")
        if os.path.exists(rb):
            try:
                p = json.load(open(rb))
                self.update_failed[p.get("to")] = time.time()
                self.event("crit", f"Update {p.get('to')} failed to start - rolled back to {VERSION}")
            except Exception:
                pass
            os.remove(rb)

    def update_ok(self):
        if os.path.exists(PENDING):
            try:
                p = json.load(open(PENDING))
                self.event("info", f"Updated {p.get('from')} -> {VERSION}")
            except Exception:
                pass
            os.remove(PENDING)

    def loop(self):
        self.latest = None
        self.check_pending()
        self.last_gpus = all_gpus()
        self.apply_startup_tuning()
        if self.cfg["manage_miner"] and self.cfg["miner_cmd"]:
            self.supervising = True
            threading.Thread(target=self.miner.supervise, daemon=True).start()
        threading.Thread(target=self.waker, daemon=True).start()
        self.event("info", f"Agent {VERSION} started")
        boot = time.time() - float(open("/proc/uptime").read().split()[0]) if os.path.exists("/proc/uptime") else time.time()
        while True:
            t0 = time.time()
            try:
                if not self.cfg["token"]:
                    self.register()
                gpus = all_gpus()
                self.last_gpus = gpus
                if self.cfg["manage_miner"]:
                    self.temp_guard(gpus)
                self.autofan_tick(gpus)
                m = self.miner
                try:
                    per = m.gpu_hashrates(gpus)
                except Exception:
                    per = {}
                for g in gpus:
                    g["hashrate"] = per.get(g["index"])
                payload = {
                    "agent_version": VERSION, "hostname": socket.gethostname(),
                    "boot_time": boot, "manage_miner": self.cfg["manage_miner"],
                    "cooldown": self.cooldown, "gpus": gpus, "hashrate": m.hashrate(),
                    "miner": {"running": m.running() if self.cfg["manage_miner"] else None,
                              "uptime": time.time() - m.started_at if m.started_at and m.running() else 0,
                              "restarts": m.restarts,
                              "name": os.path.basename(shlex.split(self.cfg["miner_cmd"])[0]) if self.cfg["miner_cmd"] else "",
                              "log": list(m.lines)[-30:]},
                    "events": self.events,
                    "flightsheet": self.cfg.get("flightsheet"),
                    "system": sysinfo(),
                }
                resp = self.post("/api/agent/report", payload)
                self.events = []
                self.update_ok()
                self.latest = resp.get("latest_version")
                if resp.get("name") and resp["name"] != self.cfg.get("display_name"):
                    self.cfg["display_name"] = resp["name"]
                    save_config(display_name=resp["name"])
                    if ("%RIG_NAME%" in self.cfg.get("miner_cmd", "") and self.miner.running()
                            and getattr(self.miner, "used_name", resp["name"]) != resp["name"]):
                        self.event("info", f"Renamed to {resp['name']}: restarting miner so the pool shows the new worker name")
                        self.miner.stop()
                for c in resp.get("commands", []):
                    try:
                        ok, out = self.execute(c)
                    except Exception as e:
                        ok, out = False, str(e)
                    self.event("info" if ok else "warn", f"Command {c['type']}: {'ok' if ok else 'failed'}")
                    try:
                        self.post("/api/agent/result", {"id": c["id"], "ok": ok, "output": out})
                    except Exception as e:
                        log("result post failed:", e)
                if (self.cfg.get("auto_update") and self.latest and not self.restart_self
                        and vtuple(self.latest) > vtuple(VERSION)):
                    self.update_to(self.latest)
                if self.restart_self:
                    try:
                        self.post("/api/agent/report", dict(payload, events=self.events))
                    except Exception:
                        pass
                    self.miner.wanted = False
                    self.miner.stop()
                    log("restarting into the new version")
                    os._exit(0)                       # systemd (Restart=always) starts the new agent
            except urllib.error.HTTPError as e:
                if e.code == 401 and self.cfg.get("token"):
                    log("token rejected - registering again")
                    self.cfg["token"] = ""
                elif e.code in (401, 429):
                    log("dashboard won't add this rig yet: turn on home plug-and-play, or set FARM_KEY in rig.conf"
                        " (trying again in 60 s)")
                    time.sleep(60)
                else:
                    log("server rejected request:", e.code,
                        e.read().decode(errors="replace")[:200] if hasattr(e, "read") else "")
            except Exception as e:
                log("report failed:", e)
            self.wake.wait(max(1, self.cfg["interval"] - (time.time() - t0)))
            self.wake.clear()


def main():
    cfg = load_config()
    agent = Agent(cfg)

    def bye(*_):
        agent.miner.wanted = False
        agent.miner.stop()
        sys.exit(0)
    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)
    agent.loop()


if __name__ == "__main__":
    main()
