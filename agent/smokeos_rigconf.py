#!/usr/bin/env python3
"""
Turns the USB's editable rig.conf (KEY=VALUE, Windows/Mac friendly) into the
agent's /etc/smokeos/agent.json. Runs at every boot before the agent.

  smokeos_rigconf.py apply          rig.conf -> agent.json
  smokeos_rigconf.py get KEY        print one value (used by the first-boot script)
"""
import json
import os
import sys

RIG_CONF = os.environ.get("SMOKEOS_RIGCONF", "/smokeos-config/rig.conf")
AGENT_JSON = os.environ.get("SMOKEOS_CONFIG", "/etc/smokeos/agent.json")


def parse(path=RIG_CONF):
    vals = {}
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:   # tolerate Notepad BOM / CRLF
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                vals[k.strip().upper()] = v
    except FileNotFoundError:
        pass
    return vals


def pairs(s):
    out = {}
    for part in (s or "").replace(" ", "").split(","):
        if ":" in part:
            k, v = part.split(":", 1)
            if k.isdigit() and v.isdigit():
                out[k] = int(v)
    return out


def hw_id():
    """MAC of the first real network card: the same rig always gets the same id."""
    base = "/sys/class/net"
    try:
        for n in sorted(os.listdir(base)):
            if n == "lo" or not os.path.exists(os.path.join(base, n, "device")):
                continue
            mac = open(os.path.join(base, n, "address")).read().strip()
            if mac and mac != "00:00:00:00:00:00":
                return mac
    except Exception:
        pass
    try:
        return open("/etc/machine-id").read().strip()[:32]
    except Exception:
        return ""


def default_name():
    h = hw_id().replace(":", "")
    return "rig-" + h[-6:] if h else "rig"


def clean_name(n):
    n = "".join(c for c in (n or "") if c.isalnum() or c in "-_")[:40]
    return n or default_name()


def apply():
    c = parse()          # an untouched/missing rig.conf is fine: home plug-and-play needs nothing
    try:
        old = json.load(open(AGENT_JSON))
    except Exception:
        old = {}
    name = clean_name(c.get("RIG_NAME"))
    cmd = c.get("MINER_CMD", "")       # %RIG_NAME% is filled in by the agent with the dashboard name
    new = dict(old)
    url = c.get("SERVER_URL", "").strip().rstrip("/")
    new.update({
        "server_url": url or old.get("server_url", ""),
        "rig_name": name,
        "farm_key": c.get("FARM_KEY", ""),
        "manage_miner": True,
        "miner_cmd": cmd,
        "power_limits": pairs(c.get("POWER_LIMITS")),
        "core_locks": pairs(c.get("CORE_LOCKS")),
        "temp_limit": int(c.get("TEMP_LIMIT") or 83),
        "cool_temp": int(c.get("COOL_TEMP") or 65),
        "auto_update": c.get("AUTO_UPDATE", "yes").lower() in ("yes", "true", "1", "on"),
    })
    # a new RIG_NAME or a different server typed into rig.conf means a fresh registration
    # (empty fields never reset anything: home plug-and-play fills them in by itself)
    if (c.get("RIG_NAME") and old.get("rig_name") and old.get("rig_name") != name) or \
            (url and old.get("server_url") and old.get("server_url") != url):
        new.pop("token", None)
    os.makedirs(os.path.dirname(AGENT_JSON), exist_ok=True)
    tmp = AGENT_JSON + ".tmp"
    with open(tmp, "w") as f:
        json.dump(new, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, AGENT_JSON)
    print(f"agent.json updated for rig '{name}'")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "get":
        print(parse().get(sys.argv[2].upper(), ""))
    elif len(sys.argv) >= 2 and sys.argv[1] == "apply":
        apply()
    else:
        print(__doc__)
