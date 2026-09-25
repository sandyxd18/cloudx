#!/usr/bin/env bash
set -euo pipefail

IMG_PATH="/var/lib/vg-cloudx.img"
VG_NAME="vg-cloudx"
SIZE_MB=10240

echo "═══ CloudX LVM Storage Bootstrap & Persistence ═══"

if [ "$(id -u)" -ne 0 ]; then
  echo "Error: Run with sudo: sudo ./scripts/setup-storage.sh" >&2
  exit 1
fi

# 1. Ensure loop image exists
if [ ! -f "$IMG_PATH" ]; then
  echo "[+] Creating $SIZE_MB MB backing file at $IMG_PATH..."
  dd if=/dev/zero of="$IMG_PATH" bs=1M count="$SIZE_MB" status=none
fi

# 2. Attach loop device if not attached
LOOP_DEV=$(losetup -j "$IMG_PATH" | cut -d: -f1 | head -n1)
if [ -z "$LOOP_DEV" ]; then
  LOOP_DEV=$(losetup --find --show "$IMG_PATH")
  echo "[+] Attached $IMG_PATH to $LOOP_DEV"
else
  echo "[+] Already attached to $LOOP_DEV"
fi

# 3. Initialize LVM PV and VG if missing
if ! vgs "$VG_NAME" >/dev/null 2>&1; then
  echo "[+] Creating Physical Volume on $LOOP_DEV..."
  pvcreate -f "$LOOP_DEV"
  echo "[+] Creating Volume Group $VG_NAME..."
  vgcreate "$VG_NAME" "$LOOP_DEV"
fi

pvscan >/dev/null 2>&1 || true
vgchange -ay "$VG_NAME" >/dev/null 2>&1 || true

echo "✓ Volume Group '$VG_NAME' is active:"
vgs "$VG_NAME"

# 4. Install systemd unit for persistent boot setup
SERVICE_FILE="/etc/systemd/system/cloudx-storage-init.service"
if [ -d "/etc/systemd/system" ]; then
  cat <<EOF > "$SERVICE_FILE"
[Unit]
Description=CloudX Persistent LVM Loop Setup
Before=docker.service libvirtd.service
After=local-fs.target

[Service]
Type=oneshot
ExecStart=/bin/bash -c 'IMG="/var/lib/vg-cloudx.img"; VG="vg-cloudx"; if [ -f "\$IMG" ]; then LOOP=\$(losetup -j "\$IMG" | cut -d: -f1 | head -n1); if [ -z "\$LOOP" ]; then LOOP=\$(losetup --find --show "\$IMG"); fi; pvscan >/dev/null 2>&1 || true; vgchange -ay "\$VG" >/dev/null 2>&1 || true; fi'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload 2>/dev/null || true
  systemctl enable cloudx-storage-init.service 2>/dev/null || true
  echo "✓ Systemd service 'cloudx-storage-init.service' installed and enabled."
fi
