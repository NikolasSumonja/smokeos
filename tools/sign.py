#!/usr/bin/env python3
"""Sign a release file:  SMOKEOS_SIGNING_KEY=<hex> python3 tools/sign.py FILE  -> FILE.sig"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "common"))
import ed25519  # noqa: E402

key = os.environ.get("SMOKEOS_SIGNING_KEY", "").strip()
if len(key) != 64:
    sys.exit("SMOKEOS_SIGNING_KEY (64 hex chars) not set")
path = sys.argv[1]
sig = ed25519.sign_file(key, path)
pub = open(os.path.join(HERE, "..", "signing.pub")).read().strip()
if not ed25519.verify_file(pub, path, sig):
    sys.exit("signature doesn't match signing.pub - wrong secret key?")
with open(path + ".sig", "w") as f:
    f.write(sig + "\n")
print(f"signed {path}")
