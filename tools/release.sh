#!/usr/bin/env bash
# Publish a new SmokeOS version:   tools/release.sh 1.2.0
# Bumps VERSION, commits, tags and pushes. GitHub Actions then builds, signs and
# publishes it; your server picks it up within 30 min and updates the rigs.
set -euo pipefail
cd "$(dirname "$0")/.."
V="${1:?usage: tools/release.sh NEW_VERSION   (current: $(cat VERSION))}"
[[ "$V" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "version must look like 1.2.0"; exit 1; }
echo "$V" > VERSION
git add -A
git commit -m "SmokeOS $V"
git tag "v$V"
git push origin HEAD "v$V"
echo "Pushed v$V - watch it build under the repo's Actions tab."
