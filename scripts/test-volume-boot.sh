#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Old per-service CLIs (networking/compute/block-store) are now one `cloudx`; scripts parse JSON.
cloudx() { command cloudx -o json "$@"; }
INTERNAL_TOKEN="${INTERNAL_TOKEN:-$(sudo grep -s '^INTERNAL_TOKEN=' /etc/cloudx/cloudx.env 2>/dev/null | cut -d= -f2 || true)}"
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
  cloudx server delete "$SERVER_NAME" >/dev/null 2>&1
  cloudx server delete "$SERVER2_NAME" >/dev/null 2>&1
  cloudx volume detach "$DATA_VOL" >/dev/null 2>&1
  cloudx volume delete "$DATA_VOL" >/dev/null 2>&1
  cloudx volume delete "$SNAP_VOL" >/dev/null 2>&1
  cloudx snapshot delete "$SNAP_NAME" >/dev/null 2>&1
  cloudx subnet delete "$SUBNET_NAME" >/dev/null 2>&1
  cloudx network delete "$NETWORK_NAME" >/dev/null 2>&1
  cloudx image delete "$IMAGE_NAME" >/dev/null 2>&1
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

cloudx network create "$NETWORK_NAME" || true
cloudx subnet create "$SUBNET_NAME" --network "$NETWORK_NAME" --cidr 10.61.0.0/24 --gateway 10.61.0.1 --dhcp || true
cloudx image upload "$IMAGE_NAME" --file "$IMAGE_PATH" --disk-format qcow2 || true
cloudx keypair create "$KEY_NAME" || true
chmod 600 "${KEY_NAME}.pem" || true
cloudx server create "$SERVER_NAME" --image "$IMAGE_NAME" --flavor tiny --network "$NETWORK_NAME" --key-name "$KEY_NAME"
for _ in $(seq 1 60); do
  st="$(cloudx server show "$SERVER_NAME" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
  [ "$st" = "ACTIVE" ] && break
  [ "$st" = "ERROR" ] && { cloudx server show "$SERVER_NAME"; exit 1; }
  sleep 3
done
root_vol="$(cloudx server show "$SERVER_NAME" | python3 -c 'import json,sys; data=json.load(sys.stdin); print(data["root_volume_id"])')"
cloudx volume show "$root_vol" | python3 -c 'import json,sys; data=json.load(sys.stdin); assert data["bootable"] and data["status"] == "in-use"'
cloudx volume create "$DATA_VOL" --size 1
cloudx volume attach "$DATA_VOL" --server "$SERVER_NAME"
cloudx volume snapshot "$DATA_VOL" --name "$SNAP_NAME"
for _ in $(seq 1 40); do [ "$(cloudx snapshot show "$SNAP_NAME" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')" = available ] && break; sleep 3; done
cloudx volume create "$SNAP_VOL" --size 1 --snapshot "$SNAP_NAME"
cloudx server delete "$SERVER_NAME"
cloudx server create "$SERVER2_NAME" --image "$IMAGE_NAME" --flavor tiny --network "$NETWORK_NAME" --volume-size 1
echo "volume-first smoke test passed"
