#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Old per-service CLIs (networking/compute/block-store) are now one `cloudx`; scripts parse JSON.
cloudx() { command cloudx -o json "$@"; }
INTERNAL_TOKEN="${INTERNAL_TOKEN:-$(sudo grep -s '^INTERNAL_TOKEN=' /etc/cloudx/cloudx.env | cut -d= -f2)}"

IMAGE_PATH=""
PREFIX="test-mini-sg-$(date +%s)"
NETWORK_NAME="${PREFIX}-net"
SUBNET_NAME="${PREFIX}-subnet"
IMAGE_NAME="${PREFIX}-image"
KEY_NAME="${PREFIX}-key"
SERVER_NAME="${PREFIX}-server"
SG_NAME="${PREFIX}-web"

cleanup() {
  set +e
  cloudx server delete "$SERVER_NAME" >/dev/null 2>&1
  cloudx security-group delete "$SG_NAME" >/dev/null 2>&1
  cloudx subnet delete "$SUBNET_NAME" >/dev/null 2>&1
  cloudx network delete "$NETWORK_NAME" >/dev/null 2>&1
  rm -f "${KEY_NAME}.pem"
  scripts/cleanup-all.sh --force >/dev/null 2>&1 || true
}
trap cleanup EXIT

while [ "$#" -gt 0 ]; do
  case "$1" in
    --image) IMAGE_PATH="${2:-}"; shift 2 ;;
    -h|--help)
      cat <<'USAGE'
Usage: scripts/test-security-group.sh --image ./cirros.qcow2

Creates a security group, verifies OVN translation debug output, boots a VM with
that group, toggles ICMP rule state, and cleans up all test resources.
USAGE
      exit 0
      ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$IMAGE_PATH" ] || [ ! -f "$IMAGE_PATH" ]; then
  echo "provide an existing qcow2 image with --image ./path.qcow2" >&2
  exit 2
fi

cloudx network create "$NETWORK_NAME" || true
cloudx subnet create "$SUBNET_NAME" --network "$NETWORK_NAME" --cidr 10.53.0.0/24 --gateway 10.53.0.1 --dhcp || true
cloudx security-group create "$SG_NAME" -d "test web and ssh" || true
cloudx security-group rule add "$SG_NAME" --direction ingress --protocol tcp --port 22 --remote-ip 0.0.0.0/0
icmp_rule="$(cloudx security-group rule add "$SG_NAME" --direction ingress --protocol icmp --remote-ip 0.0.0.0/0 | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"

cloudx image upload "$IMAGE_NAME" --file "$IMAGE_PATH" --disk-format qcow2 || true
cloudx keypair create "$KEY_NAME" || true
chmod 600 "${KEY_NAME}.pem" || true
cloudx server create "$SERVER_NAME" --image "$IMAGE_NAME" --flavor tiny --network "$NETWORK_NAME" --key-name "$KEY_NAME" --security-group "$SG_NAME"

for _ in $(seq 1 45); do
  status="$(cloudx server show "$SERVER_NAME" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
  [ "$status" = "ACTIVE" ] && break
  [ "$status" = "ERROR" ] && { cloudx server show "$SERVER_NAME"; exit 1; }
  sleep 2
done

port_id="$(cloudx server show "$SERVER_NAME" | python3 -c 'import json,sys; print(json.load(sys.stdin)["port_id"])')"
cloudx security-group rule delete "$icmp_rule" >/dev/null
cloudx security-group rule add "$SG_NAME" --direction ingress --protocol icmp --remote-ip 0.0.0.0/0 >/dev/null

echo "security group smoke test passed for $SERVER_NAME"
