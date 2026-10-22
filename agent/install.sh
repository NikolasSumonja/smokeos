#!/usr/bin/env bash
# SmokeOS agent installer for an existing Ubuntu / HiveOS rig (the USB image already has it). Run as root:
#   sudo bash install.sh                      install / update the agent
#   sudo bash install.sh --krig               also download the KRig miner (Pearl)
#   sudo bash install.sh --srb                also download SRBMiner
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run with sudo"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
KRIG_VER="1.5.10"
KRIG_URL="https://github.com/kryptex-miners-org/kryptex-miners/releases/download/krig-1-5-10/krig-miner-${KRIG_VER}-linux-x64.tar.gz"

command -v python3 >/dev/null || { apt-get update && apt-get install -y python3; }

mkdir -p /opt/smokeos /etc/smokeos
install -m 755 "$HERE/smokeos_agent.py" "$HERE/smokeos_rigconf.py" "$HERE/smokeos_guard.py" /opt/smokeos/
install -m 644 "$HERE/../common/ed25519.py" "$HERE/../VERSION" /opt/smokeos/
if [ -f "$HERE/../signing.pub" ]; then install -m 644 "$HERE/../signing.pub" /opt/smokeos/
else echo ">> No signing.pub: auto-updates disabled on this rig (run tools/keygen.py, then reinstall)"; fi

if [ ! -f /etc/smokeos/agent.json ]; then
  install -m 600 "$HERE/agent.example.json" /etc/smokeos/agent.json
  echo ">> Created /etc/smokeos/agent.json - edit it (server_url, token, miner_cmd) before starting."
  NEED_EDIT=1
else
  chmod 600 /etc/smokeos/agent.json
fi

SRB_URL="https://github.com/doktor83/SRBMiner-Multi/releases/download/3.7.7/SRBMiner-Multi-3-7-7-Linux.tar.gz"
if [ "${1:-}" = "--srb" ]; then
  echo ">> Downloading SRBMiner 3.7.7"
  tmp=$(mktemp -d)
  curl -fsSL "$SRB_URL" -o "$tmp/srb.tgz"
  tar -xf "$tmp/srb.tgz" -C "$tmp"
  bin=$(find "$tmp" -type f -name 'SRBMiner-MULTI' | head -1)
  [ -n "$bin" ] || { echo "SRBMiner-MULTI not found in archive"; exit 1; }
  mkdir -p /opt/smokeos/miners/srbminer
  cp -r "$(dirname "$bin")"/. /opt/smokeos/miners/srbminer/
  chmod +x /opt/smokeos/miners/srbminer/SRBMiner-MULTI
  rm -rf "$tmp"
  echo ">> SRBMiner installed at /opt/smokeos/miners/srbminer/SRBMiner-MULTI"
fi

if [ "${1:-}" = "--krig" ]; then
  echo ">> Downloading KRig ${KRIG_VER}"
  tmp=$(mktemp -d)
  curl -fsSL "$KRIG_URL" -o "$tmp/krig.tar.gz"
  mkdir -p /opt/smokeos/miners/krig
  tar -xzf "$tmp/krig.tar.gz" -C "$tmp"
  bin=$(find "$tmp" -type f -name 'krig-miner' | head -1)
  [ -n "$bin" ] || { echo "krig-miner binary not found in archive"; exit 1; }
  cp -r "$(dirname "$bin")"/. /opt/smokeos/miners/krig/
  chmod +x /opt/smokeos/miners/krig/krig-miner
  rm -rf "$tmp"
  echo ">> KRig installed at /opt/smokeos/miners/krig/krig-miner"
fi

install -m 644 "$HERE/smokeos-agent.service" /etc/systemd/system/smokeos-agent.service
systemctl daemon-reload
systemctl enable smokeos-agent >/dev/null

if [ "${NEED_EDIT:-0}" = 1 ]; then
  echo ">> Edit /etc/smokeos/agent.json, then run: systemctl start smokeos-agent"
else
  systemctl restart smokeos-agent
  echo ">> Agent running. Logs: journalctl -u smokeos-agent -f"
fi
