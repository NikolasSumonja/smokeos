#!/usr/bin/env python3
"""
Create the SmokeOS release signing key (run ONCE, on your own computer).

  python3 tools/keygen.py

- Writes signing.pub (public key) into the repo. Commit it.
- Prints the SECRET key. Put it in GitHub -> repo Settings -> Secrets and variables
  -> Actions -> New repository secret, named SMOKEOS_SIGNING_KEY. Keep a backup in
  your password manager. Never commit it or paste it anywhere else.

Rigs only install releases signed with this key, so even if someone tampered with
a download (or your dashboard server), they couldn't push code to your rigs.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "common"))
import ed25519  # noqa: E402

PUB = os.path.join(HERE, "..", "signing.pub")
if os.path.exists(PUB) and "--force" not in sys.argv:
    sys.exit("signing.pub already exists. Use --force to replace it (rigs then need a manual reinstall).")
secret = os.urandom(32)
with open(PUB, "w") as f:
    f.write(ed25519.public_key(secret).hex() + "\n")
print("Public key written to signing.pub - commit it.\n")
print("SECRET key (GitHub secret SMOKEOS_SIGNING_KEY):\n")
print("   " + secret.hex() + "\n")
