#!/usr/bin/env python3
"""
Update safety net. systemd runs this before every agent start (ExecStartPre).
It is never replaced by auto-updates, so even an update that can't start at all
gets rolled back: after 3 failed starts, the previous version is restored.
"""
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
PENDING = os.path.join(HERE, ".pending_update")
BACKUP = os.path.join(HERE, ".backup")

try:
    with open(PENDING) as f:
        p = json.load(f)
except FileNotFoundError:
    raise SystemExit(0)
except Exception:
    os.remove(PENDING)
    raise SystemExit(0)

p["starts"] = p.get("starts", 0) + 1
if p["starts"] <= 3:
    with open(PENDING, "w") as f:
        json.dump(p, f)
    raise SystemExit(0)

try:
    with open(os.path.join(BACKUP, "manifest.json")) as f:
        manifest = json.load(f)
    for name, dest in manifest.items():
        shutil.copy2(os.path.join(BACKUP, name), dest)
    print(f"SmokeOS update {p.get('to')} failed to start 3 times - rolled back to {p.get('from')}")
    with open(os.path.join(HERE, ".rolled_back"), "w") as f:
        json.dump(p, f)
except Exception as e:
    print("rollback failed:", e)
os.remove(PENDING)
