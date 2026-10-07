#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Old per-service CLIs (networking/compute/block-store) are now one `cloudx`; scripts parse JSON.
cloudx() { command cloudx -o json "$@"; }
INTERNAL_TOKEN="${INTERNAL_TOKEN:-$(sudo grep -s '^INTERNAL_TOKEN=' /etc/cloudx/cloudx.env | cut -d= -f2)}"

IMAGE_PATH=""
PREFIX="test-mini-fip-$(date +%s)"
NETWORK_NAME="${PREFIX}-net"
SUBNET_NAME="${PREFIX}-subnet"
ROUTER_NAME="${PREFIX}-router"
IMAGE_NAME="${PREFIX}-image"
KEY_NAME="${PREFIX}-key"
SERVER_NAME="${PREFIX}-server"
FIP_REF=""

cleanup() {
  set +e
  [ -n "$FIP_REF" ] && cloudx floating-ip delete "$FIP_REF" >/dev/null 2>&1
  cloudx server delete "$SERVER_NAME" >/dev/null 2>&1
  cloudx router remove-interface "$ROUTER_NAME" "$SUBNET_NAME" >/dev/null 2>&1
  cloudx router delete "$ROUTER_NAME" >/dev/null 2>&1
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
Usage: scripts/test-floating-ip.sh --image ./cirros.qcow2

Creates a private VM, allocates a floating IP from public-net, associates it,
verifies OVN NAT metadata, then cleans up.
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

cloudx network show "${PROVIDER_NETWORK_NAME:-public-net}" >/dev/null
cloudx network create "$NETWORK_NAME"
cloudx subnet create "$SUBNET_NAME" --network "$NETWORK_NAME" --cidr 10.77.0.0/24 --gateway 10.77.0.1 --dhcp
cloudx router create "$ROUTER_NAME"
cloudx router add-interface "$ROUTER_NAME" --subnet "$SUBNET_NAME"
cloudx router set-gateway "$ROUTER_NAME" "${PROVIDER_NETWORK_NAME:-public-net}"
cloudx image upload "$IMAGE_NAME" --file "$IMAGE_PATH" --disk-format qcow2 || true
cloudx keypair create "$KEY_NAME" || true
chmod 600 "${KEY_NAME}.pem" || true
cloudx server create "$SERVER_NAME" --image "$IMAGE_NAME" --flavor tiny --network "$NETWORK_NAME" --key-name "$KEY_NAME"

FIP_REF="$(cloudx floating-ip create "${PROVIDER_NETWORK_NAME:-public-net}" | python3 -c 'import json,sys; data=json.load(sys.stdin); print(data["floating_ip_address"] or data["floating_ip"])')"
cloudx floating-ip associate "$FIP_REF" --server "$SERVER_NAME" --router "$ROUTER_NAME"

if docker compose ps ovn-nb-db >/dev/null 2>&1; then
  docker compose exec -T networking-api ovn-nbctl --db=tcp:ovn-nb-db:6641 lr-nat-list "$ROUTER_NAME" | grep -F "$FIP_REF"
fi

echo "floating IP test completed: $FIP_REF"
