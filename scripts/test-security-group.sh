#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

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
  compute server delete "$SERVER_NAME" >/dev/null 2>&1
  networking security-group delete "$SG_NAME" >/dev/null 2>&1
  networking subnet delete "$SUBNET_NAME" >/dev/null 2>&1
  networking network delete "$NETWORK_NAME" >/dev/null 2>&1
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

networking network create "$NETWORK_NAME" || true
networking subnet create "$SUBNET_NAME" --network "$NETWORK_NAME" --cidr 10.53.0.0/24 --gateway 10.53.0.1 --dhcp || true
networking security-group create "$SG_NAME" --description "test web and ssh" || true
networking security-group rule create "$SG_NAME" --direction ingress --protocol tcp --port 22 --remote-ip 0.0.0.0/0
icmp_rule="$(networking security-group rule create "$SG_NAME" --direction ingress --protocol icmp --remote-ip 0.0.0.0/0 | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
networking security-group ovn-show "$SG_NAME" | python3 -c 'import json,sys; data=json.load(sys.stdin); assert data["port_group"] and data["translated_rules"]'

image upload "$IMAGE_NAME" --file "$IMAGE_PATH" --disk-format qcow2 || true
compute keypair create "$KEY_NAME" > "${KEY_NAME}.pem" || true
chmod 600 "${KEY_NAME}.pem" || true
compute server create "$SERVER_NAME" --image "$IMAGE_NAME" --flavor tiny --network "$NETWORK_NAME" --key-name "$KEY_NAME" --security-group "$SG_NAME"

for _ in $(seq 1 45); do
  status="$(compute server show "$SERVER_NAME" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
  [ "$status" = "ACTIVE" ] && break
  [ "$status" = "ERROR" ] && { compute server show "$SERVER_NAME"; exit 1; }
  sleep 2
done

port_id="$(compute server show "$SERVER_NAME" | python3 -c 'import json,sys; print(json.load(sys.stdin)["port_id"])')"
networking port ovn-show "$port_id" >/dev/null
networking security-group rule delete "$icmp_rule" >/dev/null
networking security-group rule create "$SG_NAME" --direction ingress --protocol icmp --remote-ip 0.0.0.0/0 >/dev/null

echo "security group smoke test passed for $SERVER_NAME"
