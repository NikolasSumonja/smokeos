#!/usr/bin/env bash
# =============================================================================
#  Build the SmokeOS USB image (flash it with balenaEtcher).
#
#  Run as root on Ubuntu 22.04/24.04 (or let GitHub Actions do it on release):
#     sudo image/build-image.sh            NVIDIA image (RTX, GTX, P106, CMP)
#     sudo image/build-image.sh --amd      + AMD ROCm for RX 6000+ (bigger, untested)
#
#  Output: dist/smokeos-<version>.img.xz
#
#  Stages: "base" (Ubuntu + drivers, cached in build/base-*.tar) and
#  "assemble" (partitions, SmokeOS files, bootloaders). Re-runs reuse the cache.
# =============================================================================
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
VERSION=$(cat "$ROOT/VERSION")
SUITE=noble
MIRROR=${MIRROR:-http://archive.ubuntu.com/ubuntu}
NV=${NV:-580-server}           # 580 = last branch with Pascal (P106/GTX 10) + supports RTX 50
AMD=0; COMPRESS=1
for a in "$@"; do case "$a" in --amd) AMD=1 ;; --no-compress) COMPRESS=0 ;; esac; done
FLAVOR=$([ $AMD = 1 ] && echo "nvidia-amd" || echo "nvidia")
WORK="$ROOT/build"
BASE_TAR="$WORK/base-$SUITE-$FLAVOR.tar"
OUT="$ROOT/dist/smokeos-$VERSION.img"
[ $AMD = 1 ] && OUT="$ROOT/dist/smokeos-$VERSION-amd.img"

[ "$(id -u)" = 0 ] || { echo "run as root (sudo)"; exit 1; }
log() { echo -e "\n\033[1;33m==> $*\033[0m"; }
mkdir -p "$WORK" "$ROOT/dist"

need() { command -v "$1" >/dev/null || MISSING="$MISSING $2"; }
MISSING=""
need debootstrap debootstrap; need sfdisk fdisk; need mkfs.vfat dosfstools; need mkfs.ext4 e2fsprogs
need xz xz-utils; need mcopy mtools; need grub-mkimage grub-common; need curl curl
[ -d /usr/lib/grub/i386-pc ] || MISSING="$MISSING grub-pc-bin"
[ -d /usr/lib/grub/x86_64-efi ] || MISSING="$MISSING grub-efi-amd64-bin"
if [ -n "$MISSING" ]; then log "installing build tools:$MISSING"; apt-get update -qq; apt-get install -y -qq $MISSING; fi

CHROOT=""
cleanup() {
  set +e
  if [ -n "$CHROOT" ]; then
    for m in dev/pts dev proc sys run; do umount -lf "$CHROOT/$m" 2>/dev/null; done
  fi
  umount -lf "$WORK/mnt/root" 2>/dev/null
  for l in ${LOOPS:-}; do losetup -d "$l" 2>/dev/null; done
}
trap cleanup EXIT

chroot_mounts() {
  CHROOT=$1
  mount --bind /dev "$CHROOT/dev"; mount --bind /dev/pts "$CHROOT/dev/pts"
  mount -t proc proc "$CHROOT/proc"; mount -t sysfs sys "$CHROOT/sys"; mount -t tmpfs tmpfs "$CHROOT/run"
}
chroot_umounts() {
  for m in dev/pts dev proc sys run; do umount -lf "$CHROOT/$m" 2>/dev/null || true; done
  CHROOT=""
}

# =============================================================================
#  Stage 1: base system
# =============================================================================
if [ ! -f "$BASE_TAR" ]; then
  log "Stage 1: building Ubuntu $SUITE base ($FLAVOR)"
  R="$WORK/rootfs"
  rm -rf "$R"; mkdir -p "$R"
  debootstrap --variant=minbase --components=main,restricted,universe,multiverse "$SUITE" "$R" "$MIRROR"

  cat > "$R/etc/apt/sources.list" <<EOF
deb $MIRROR $SUITE main restricted universe multiverse
deb $MIRROR $SUITE-updates main restricted universe multiverse
deb $MIRROR $SUITE-security main restricted universe multiverse
EOF
  printf 'APT::Install-Recommends "false";\nAPT::Install-Suggests "false";\n' > "$R/etc/apt/apt.conf.d/90smokeos"
  cp /etc/resolv.conf "$R/etc/resolv.conf"
  # don't let package scripts start services inside the chroot
  printf '#!/bin/sh\nexit 101\n' > "$R/usr/sbin/policy-rc.d"; chmod +x "$R/usr/sbin/policy-rc.d"

  chroot_mounts "$R"
  chroot "$R" /bin/bash -e <<EOF
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq linux-image-generic initramfs-tools linux-firmware \
  systemd-sysv systemd-resolved systemd-timesyncd udev dbus kmod \
  openssh-server python3 curl ca-certificates gnupg pciutils usbutils \
  cloud-guest-utils fdisk e2fsprogs dosfstools nano htop lm-sensors \
  iproute2 iputils-ping less tzdata
apt-get install -y -qq linux-modules-nvidia-$NV-generic nvidia-kernel-common-$NV \
  nvidia-utils-$NV libnvidia-compute-$NV nvidia-compute-utils-$NV
# keep kernel + NVIDIA modules in lockstep: no surprise kernel upgrades
apt-mark hold linux-image-generic linux-modules-nvidia-$NV-generic >/dev/null
EOF
  if [ $AMD = 1 ]; then
    log "adding AMD ROCm (runtime only)"
    chroot "$R" /bin/bash -e <<'EOF'
export DEBIAN_FRONTEND=noninteractive
mkdir -p /etc/apt/keyrings
curl -fsSL https://repo.radeon.com/rocm/rocm.gpg.key | gpg --dearmor > /etc/apt/keyrings/rocm.gpg
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/rocm.gpg] https://repo.radeon.com/rocm/apt/6.2.4 noble main" > /etc/apt/sources.list.d/rocm.list
printf 'Package: *\nPin: release o=repo.radeon.com\nPin-Priority: 600\n' > /etc/apt/preferences.d/rocm-pin-600
apt-get update -qq
apt-get install -y -qq hip-runtime-amd rocm-smi-lib rocminfo
ln -sf /opt/rocm/bin/rocm-smi /usr/local/bin/rocm-smi
EOF
  fi
  chroot "$R" /bin/bash -e <<'EOF'
systemctl enable systemd-networkd systemd-resolved systemd-timesyncd >/dev/null 2>&1
ln -sf /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf
echo "blacklist nouveau" > /etc/modprobe.d/blacklist-nouveau.conf
echo "options nouveau modeset=0" >> /etc/modprobe.d/blacklist-nouveau.conf
update-initramfs -u -k all >/dev/null
apt-get clean
rm -rf /var/lib/apt/lists/* /var/log/*.log /tmp/*
EOF
  chroot_umounts
  rm -f "$R/usr/sbin/policy-rc.d"
  log "packing base cache"
  tar -C "$R" --numeric-owner -cpf "$BASE_TAR" .
  rm -rf "$R"
else
  log "Stage 1: using cached base $(basename "$BASE_TAR")"
fi

# =============================================================================
#  Stage 2: assemble the disk image
# =============================================================================
log "Stage 2: assembling $(basename "$OUT")"
BASE_MB=$(( $(stat -c %s "$BASE_TAR") / 1048576 ))
ROOT_MB=$(( BASE_MB + BASE_MB / 5 + 600 ))          # room for miners + first updates
# fixed layout (sectors of 512 bytes). Classic MBR partition table like HiveOS uses:
# old mining-board BIOSes (legacy USB boot) misread GPT sticks, and Windows shows the
# SMOKEOS drive properly. UEFI boots MBR disks fine (ESP = type EF).
P1=1;       P1S=2047                                 # gap after the MBR: GRUB core.img (legacy BIOS)
P2=2048;    P2S=$((256*2048))                        # EFI system partition
P3=$((P2+P2S)); P3S=$((64*2048))                     # SMOKEOS config (FAT, editable on any PC)
P4=$((P3+P3S)); P4S=$((ROOT_MB*2048))                # root (ext4, grows on first boot)
TOTAL=$(( (P4 + P4S + 2048) * 512 ))

rm -f "$OUT" "$OUT.xz"
truncate -s "$TOTAL" "$OUT"
sfdisk -q "$OUT" <<EOF
label: dos
start=$P2, size=$P2S, type=ef, bootable
start=$P3, size=$P3S, type=e
start=$P4, size=$P4S, type=83
EOF

loop() { losetup -f --show -o $(( $1 * 512 )) --sizelimit $(( $2 * 512 )) "$OUT"; }
LEFI=$(loop $P2 $P2S); LCFG=$(loop $P3 $P3S); LROOT=$(loop $P4 $P4S)
LOOPS="$LEFI $LCFG $LROOT"
mkfs.vfat -F 32 -n SMKEFI "$LEFI" >/dev/null
mkfs.vfat -F 16 -n SMOKEOS "$LCFG" >/dev/null
mkfs.ext4 -q -L smokeos-root -O ^metadata_csum_seed "$LROOT"

# FAT partitions are filled with mtools (no kernel vfat needed); ext4 root is mounted
losetup -d "$LEFI" "$LCFG"; LOOPS="$LROOT"
EFI_AT="$OUT@@$(( P2 * 512 ))"; CFG_AT="$OUT@@$(( P3 * 512 ))"
M="$WORK/mnt"; mkdir -p "$M/root"
mount "$LROOT" "$M/root"
R="$M/root"

log "extracting base system"
tar -C "$R" --numeric-owner -xpf "$BASE_TAR"
cp -a "$ROOT/image/rootfs/." "$R/"
chmod 755 "$R/usr/local/sbin/smokeos-boot"   # exec bits can be lost when committed from Windows

log "installing SmokeOS $VERSION"
install -d -m 755 "$R/opt/smokeos" "$R/etc/smokeos" "$R/smokeos-config" "$R/opt/smokeos/miners"
install -m 755 "$ROOT/agent/smokeos_agent.py" "$ROOT/agent/smokeos_rigconf.py" "$ROOT/agent/smokeos_guard.py" "$R/opt/smokeos/"
install -m 644 "$ROOT/common/ed25519.py" "$ROOT/VERSION" "$R/opt/smokeos/"
[ -f "$ROOT/signing.pub" ] && install -m 644 "$ROOT/signing.pub" "$R/opt/smokeos/" \
  || echo "  (no signing.pub - auto-updates disabled in this image; run tools/keygen.py first)"
install -m 644 "$ROOT/agent/smokeos-agent.service" "$R/etc/systemd/system/"
mkdir -p "$R/etc/systemd/system/multi-user.target.wants"
ln -sf /etc/systemd/system/smokeos-agent.service "$R/etc/systemd/system/multi-user.target.wants/"
ln -sf /etc/systemd/system/smokeos-boot.service "$R/etc/systemd/system/multi-user.target.wants/"

log "downloading miners"
fetch_miner() {   # name url binary
  local tmp; tmp=$(mktemp -d)
  if curl -fsSL "$2" -o "$tmp/m.tgz" && tar -xf "$tmp/m.tgz" -C "$tmp"; then
    local bin; bin=$(find "$tmp" -type f -name "$3" | head -1)
    if [ -n "$bin" ]; then
      mkdir -p "$R/opt/smokeos/miners/$1"; cp -r "$(dirname "$bin")"/. "$R/opt/smokeos/miners/$1/"
      chmod +x "$R/opt/smokeos/miners/$1/$3"; echo "  $1 ok"
    else echo "  $1: binary $3 not found in archive"; fi
  else echo "  $1: download failed (can be added later)"; fi
  rm -rf "$tmp"
}
fetch_miner krig "https://github.com/kryptex-miners-org/kryptex-miners/releases/download/krig-1-5-10/krig-miner-1.5.10-linux-x64.tar.gz" krig-miner
fetch_miner t-rex "https://github.com/trexminer/T-Rex/releases/download/0.26.8/t-rex-0.26.8-linux.tar.gz" t-rex
# SRBMiner: newest release if the GitHub API answers, otherwise a known-good version
SRB_URL=$(curl -fsSL https://api.github.com/repos/doktor83/SRBMiner-Multi/releases/latest 2>/dev/null | python3 -c "
import json,sys
for a in json.load(sys.stdin).get('assets', []):
    if a['name'].endswith('Linux.tar.gz') or a['name'].endswith('Linux.tar.xz'): print(a['browser_download_url']); break" 2>/dev/null || true)
fetch_miner srbminer "${SRB_URL:-https://github.com/doktor83/SRBMiner-Multi/releases/download/3.7.7/SRBMiner-Multi-3-7-7-Linux.tar.gz}" SRBMiner-MULTI

log "Tailscale (static build; the rig installs it on first boot if this fails)"
TS_TGZ=$(curl -fsSL "https://pkgs.tailscale.com/stable/?mode=json" 2>/dev/null | python3 -c "import json,sys;print(json.load(sys.stdin)['Tarballs']['amd64'])" 2>/dev/null || true)
if [ -n "$TS_TGZ" ] && curl -fsSL "https://pkgs.tailscale.com/stable/$TS_TGZ" -o "$WORK/ts.tgz"; then
  tsd=$(mktemp -d); tar -xzf "$WORK/ts.tgz" -C "$tsd"; tsd=$(find "$tsd" -maxdepth 1 -mindepth 1 -type d | head -1)
  install -m 755 "$tsd/tailscale" "$tsd/tailscaled" "$R/usr/sbin/"
  ln -sf /usr/sbin/tailscale "$R/usr/bin/tailscale"
  install -m 644 "$tsd/systemd/tailscaled.service" "$R/etc/systemd/system/"
  install -D -m 644 "$tsd/systemd/tailscaled.defaults" "$R/etc/default/tailscaled"
  ln -sf /etc/systemd/system/tailscaled.service "$R/etc/systemd/system/multi-user.target.wants/"
  echo "  $TS_TGZ installed"; rm -rf "$tsd" "$WORK/ts.tgz"
else
  echo "  could not download Tailscale here"
fi

log "system config"
# SSH: Ubuntu 24.04 uses socket activation; a separately enabled ssh.service fights it for port 22
rm -f "$R/etc/systemd/system/multi-user.target.wants/ssh.service" "$R/etc/systemd/system/sshd.service"
cat > "$R/etc/fstab" <<EOF
LABEL=smokeos-root  /                ext4  defaults,noatime,commit=60  0 1
tmpfs               /tmp             tmpfs defaults,size=256M  0 0
EOF
mkdir -p "$R/boot/efi"
echo "smokeos" > "$R/etc/hostname"
printf '127.0.0.1 localhost\n127.0.1.1 smokeos\n' > "$R/etc/hosts"
: > "$R/etc/machine-id"
rm -f "$R"/etc/ssh/ssh_host_*
echo "$VERSION" > "$R/etc/smokeos-release"
cat > "$R/etc/issue" <<'EOF'
SmokeOS \n  (\l)
Manage this rig from your SmokeOS dashboard.

EOF

log "bootloaders (UEFI + legacy BIOS)"
G="$R/boot/grub"; mkdir -p "$G"
cat > "$G/grub.cfg" <<'EOF'
set timeout=2
set default=0
search --no-floppy --label smokeos-root --set=root
menuentry "SmokeOS" {
  linux /boot/vmlinuz root=LABEL=smokeos-root ro quiet panic=10 pci=noaer console=tty0 console=ttyS0,115200
  initrd /boot/initrd.img
}
EOF
# UEFI: one self-contained GRUB at the removable-media path
cat > "$WORK/efi-early.cfg" <<'EOF'
search --no-floppy --label smokeos-root --set=root
configfile ($root)/boot/grub/grub.cfg
EOF
grub-mkstandalone -O x86_64-efi -o "$WORK/BOOTX64.EFI" \
  --modules="part_gpt part_msdos ext2 fat search search_label configfile linux normal echo test all_video" \
  "boot/grub/grub.cfg=$WORK/efi-early.cfg"
MTOOLS_SKIP_CHECK=1 mmd -i "$EFI_AT" ::/EFI ::/EFI/BOOT
MTOOLS_SKIP_CHECK=1 mcopy -i "$EFI_AT" "$WORK/BOOTX64.EFI" ::/EFI/BOOT/BOOTX64.EFI
# Legacy BIOS: core.img in the BIOS-boot partition, boot.img in the MBR
mkdir -p "$G/i386-pc"; cp /usr/lib/grub/i386-pc/*.mod /usr/lib/grub/i386-pc/*.lst "$G/i386-pc/"
cat > "$WORK/bios-early.cfg" <<'EOF'
search --no-floppy --label smokeos-root --set=root
set prefix=($root)/boot/grub
EOF
grub-mkimage -O i386-pc -o "$WORK/core.img" -c "$WORK/bios-early.cfg" -p "/boot/grub" \
  biosdisk part_msdos part_gpt ext2 search search_label normal configfile linux echo test
python3 - "$OUT" "$WORK/core.img" "$P1" "$P1S" <<'PY'
import struct, sys
img, core_path, start, size = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
core = bytearray(open(core_path, "rb").read())
sectors = (len(core) + 511) // 512
assert sectors <= size, f"core.img too big ({sectors} sectors)"
# diskboot.img blocklist (last 12 bytes of core's first sector): where the rest of core.img lives
struct.pack_into("<QH", core, 512 - 12, start + 1, sectors - 1)
boot = bytearray(open("/usr/lib/grub/i386-pc/boot.img", "rb").read())
struct.pack_into("<Q", boot, 0x5C, start)          # boot.img -> first sector of core.img
with open(img, "r+b") as f:
    f.seek(0); f.write(boot[:440])                   # keep the partition table intact
    f.seek(start * 512); f.write(core)
print(f"  BIOS core.img: {sectors} sectors at LBA {start}")
PY

log "config partition (SMOKEOS)"
MTOOLS_SKIP_CHECK=1 mcopy -i "$CFG_AT" "$ROOT/image/config-partition/rig.conf" "$ROOT/image/config-partition/README.txt" ::/

sync
umount "$M/root"
for l in $LOOPS; do losetup -d "$l"; done; LOOPS=""

if [ $COMPRESS = 0 ]; then ls -lh "$OUT"; echo "Done (uncompressed): $OUT"; exit 0; fi
log "compressing"
xz -T0 -6 -f "$OUT"
ls -lh "$OUT.xz"
echo -e "\nDone: $OUT.xz  (flash with balenaEtcher, then edit rig.conf on the SMOKEOS drive)"
