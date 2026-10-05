#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
IMAGE_PATH=""
PREFIX="test-mini-volume-$(date +%s)"
NETWORK_NAME="${PREFIX}-net"
SUBNET_NAME="${PREFIX}-subnet"
IMAGE_NAME="${PREFIX}-image"
SERVER_NAME="${PREFIX}-server"
SERVER2_NAME="${PREFIX}-server2"
DATA_VOL="${PREFIX}-data"
SNAP_NAME="${PREFIX}-snap"
SNAP_VOL="${PREFIX}-snapvol"
KEY_NAME="${PREFIX}-key"

cleanup() {
  set +e
  compute server delete "$SERVER_NAME" >/dev/null 2>&1
  compute server delete "$SERVER2_NAME" >/dev/null 2>&1
  block-store volume force-detach "$DATA_VOL" >/dev/null 2>&1
  block-store volume delete "$DATA_VOL" >/dev/null 2>&1
  block-store volume delete "$SNAP_VOL" >/dev/null 2>&1
  block-store snapshot delete "$SNAP_NAME" >/dev/null 2>&1
  networking subnet delete "$SUBNET_NAME" >/dev/null 2>&1
  networking network delete "$NETWORK_NAME" >/dev/null 2>&1
  image delete "$IMAGE_NAME" >/dev/null 2>&1
  rm -f "${KEY_NAME}.pem"
  scripts/cleanup-all.sh --force >/dev/null 2>&1 || true
}
trap cleanup EXIT

while [ "$#" -gt 0 ]; do
  case "$1" in
    --image) IMAGE_PATH="${2:-}"; shift 2 ;;
    -h|--help) echo "Usage: scripts/test-volume-boot.sh --image ./cirros.qcow2"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$IMAGE_PATH" ] && [ -f "$IMAGE_PATH" ] || { echo "provide --image ./path.qcow2" >&2; exit 2; }

networking network create "$NETWORK_NAME" || true
networking subnet create "$SUBNET_NAME" --network "$NETWORK_NAME" --cidr 10.61.0.0/24 --gateway 10.61.0.1 --dhcp || true
image upload "$IMAGE_NAME" --file "$IMAGE_PATH" --disk-format qcow2 || true
compute keypair create "$KEY_NAME" > "${KEY_NAME}.pem" || true
chmod 600 "${KEY_NAME}.pem" || true
compute server create "$SERVER_NAME" --image "$IMAGE_NAME" --flavor tiny --network "$NETWORK_NAME" --key-name "$KEY_NAME"
root_vol="$(compute server show "$SERVER_NAME" | python3 -c 'import json,sys; data=json.load(sys.stdin); print(data["root_volume_id"])')"
block-store volume show "$root_vol" | python3 -c 'import json,sys; data=json.load(sys.stdin); assert data["bootable"] and data["status"] == "in-use"'
block-store volume create "$DATA_VOL" --size 1
block-store volume attach "$DATA_VOL" --server "$SERVER_NAME"
block-store snapshot create "$DATA_VOL" "$SNAP_NAME"
block-store volume create "$SNAP_VOL" --size 1 --snapshot "$SNAP_NAME"
compute server delete "$SERVER_NAME" --keep-root-volume
block-store volume show "$root_vol" | python3 -c 'import json,sys; data=json.load(sys.stdin); assert data["status"] == "available" and data["bootable"]'
compute server create "$SERVER2_NAME" --boot-volume "$root_vol" --flavor tiny --network "$NETWORK_NAME"
echo "volume-first smoke test passed"
