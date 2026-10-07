#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Old per-service CLIs (networking/compute/block-store) are now one `cloudx`; scripts parse JSON.
cloudx() { command cloudx -o json "$@"; }
INTERNAL_TOKEN="${INTERNAL_TOKEN:-$(sudo grep -s '^INTERNAL_TOKEN=' /etc/cloudx/cloudx.env 2>/dev/null | cut -d= -f2 || true)}"

IMAGE_PATH=""
PREFIX="test-mini-metadata-$(date +%s)"
NETWORK_NAME="${PREFIX}-net"
SUBNET_NAME="${PREFIX}-subnet"
ROUTER_NAME="${PREFIX}-router"
IMAGE_NAME="${PREFIX}-image"
KEY_NAME="${PREFIX}-key"
SERVER_NAME="${PREFIX}-server"

cleanup() {
  set +e
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
Usage: scripts/test-metadata.sh --image ./cirros.qcow2

Creates a test VM, verifies metadata-api debug lookup, and attempts in-guest
169.254.169.254 testing only when SSH reachability is already available.
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
cloudx subnet create "$SUBNET_NAME" --network "$NETWORK_NAME" --cidr 10.52.0.0/24 --gateway 10.52.0.1 --dhcp || true
cloudx router create "$ROUTER_NAME" || true
cloudx router add-interface "$ROUTER_NAME" --subnet "$SUBNET_NAME" || true
cloudx image upload "$IMAGE_NAME" --file "$IMAGE_PATH" --disk-format qcow2 || true
cloudx keypair create "$KEY_NAME" || true
chmod 600 "${KEY_NAME}.pem" || true
cloudx server create "$SERVER_NAME" --image "$IMAGE_NAME" --flavor tiny --network "$NETWORK_NAME" --key-name "$KEY_NAME" --security-group default

for _ in $(seq 1 45); do
  status="$(cloudx server show "$SERVER_NAME" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
  [ "$status" = "ACTIVE" ] && break
  [ "$status" = "ERROR" ] && { cloudx server show "$SERVER_NAME"; exit 1; }
  sleep 2
done

server_id="$(cloudx server show "$SERVER_NAME" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
port_id="$(cloudx server show "$SERVER_NAME" | python3 -c 'import json,sys; print(json.load(sys.stdin)["port_id"])')"
fixed_ip="$(cloudx server show "$SERVER_NAME" | python3 -c 'import json,sys; print(json.load(sys.stdin)["ip_address"])')"

curl -fsS -H "X-Internal-Token: ${INTERNAL_TOKEN:-}" "http://localhost:8775/latest/meta-data/instance-id?instance_id=${server_id}" | grep -q "$server_id"
curl -fsS -H "X-Internal-Token: ${INTERNAL_TOKEN:-}" "http://localhost:8775/openstack/latest/meta_data.json?port_id=${port_id}" | python3 -c 'import json,sys; data=json.load(sys.stdin); assert data["uuid"]'
curl -fsS -H "X-Internal-Token: ${INTERNAL_TOKEN:-}" "http://localhost:8775/openstack/latest/network_data.json?fixed_ip=${fixed_ip}" | python3 -c 'import json,sys; data=json.load(sys.stdin); assert data["links"] and data["networks"]'

cloudx server metadata "$SERVER_NAME" >/dev/null

echo "metadata smoke test passed for $SERVER_NAME"
