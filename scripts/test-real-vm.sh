#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

IMAGE_PATH=""
PREFIX="test-mini-$(date +%s)"
NETWORK_NAME="${PREFIX}-net"
SUBNET_NAME="${PREFIX}-subnet"
ROUTER_NAME="${PREFIX}-router"
IMAGE_NAME="${PREFIX}-image"
KEY_NAME="${PREFIX}-key"
SERVER_NAME="${PREFIX}-server"

cleanup() {
  set +e
  compute server delete "$SERVER_NAME" >/dev/null 2>&1
  block-store volume delete "${PREFIX}-vol" >/dev/null 2>&1
  networking floating-ip delete "${PREFIX}-fip" >/dev/null 2>&1
  networking router remove-interface "$ROUTER_NAME" --subnet "$SUBNET_NAME" >/dev/null 2>&1
  networking router delete "$ROUTER_NAME" >/dev/null 2>&1
  networking subnet delete "$SUBNET_NAME" >/dev/null 2>&1
  networking network delete "$NETWORK_NAME" >/dev/null 2>&1
  rm -f "${KEY_NAME}.pem"
  scripts/cleanup-all.sh --force >/dev/null 2>&1 || true
}
trap cleanup EXIT

while [ "$#" -gt 0 ]; do
  case "$1" in
    --image)
      IMAGE_PATH="${2:-}"
      shift 2
      ;;
    -h|--help)
      cat <<'USAGE'
Usage: scripts/test-real-vm.sh --image ./cirros.qcow2

Optional integration smoke test for libvirt backend. It does not download
images automatically.
USAGE
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [ -z "$IMAGE_PATH" ] || [ ! -f "$IMAGE_PATH" ]; then
  echo "provide an existing qcow2 image with --image ./path.qcow2" >&2
  exit 2
fi

[ -e /dev/kvm ] || { echo "missing /dev/kvm; skipping real VM test" >&2; exit 1; }
virsh -c "${LIBVIRT_URI:-qemu:///system}" list --all >/dev/null
ovs-vsctl br-exists "${OVS_BRIDGE:-br-int}"

networking network create "$NETWORK_NAME" || true
networking subnet create "$SUBNET_NAME" --network "$NETWORK_NAME" --cidr 10.42.0.0/24 --gateway 10.42.0.1 --dhcp || true
networking router create "$ROUTER_NAME" || true
networking router add-interface "$ROUTER_NAME" --subnet "$SUBNET_NAME" || true
image upload "$IMAGE_NAME" --file "$IMAGE_PATH" --disk-format qcow2 || true
compute keypair create "$KEY_NAME" > "${KEY_NAME}.pem" || true
chmod 600 "${KEY_NAME}.pem" || true
compute server create "$SERVER_NAME" --image "$IMAGE_NAME" --flavor tiny --network "$NETWORK_NAME" --key-name "$KEY_NAME"

for _ in $(seq 1 30); do
  status="$(compute server show "$SERVER_NAME" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
  [ "$status" = "ACTIVE" ] && break
  [ "$status" = "ERROR" ] && { compute server show "$SERVER_NAME"; exit 1; }
  sleep 2
done

virsh -c "${LIBVIRT_URI:-qemu:///system}" list --all
ovs-vsctl show
compute server ssh-info "$SERVER_NAME"
server_id="$(compute server show "$SERVER_NAME" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
curl -fsS "http://localhost:8775/openstack/latest/meta_data.json?instance_id=${server_id}" >/dev/null
compute server delete "$SERVER_NAME" || true

if virsh -c "${LIBVIRT_URI:-qemu:///system}" list --all --name | grep -q "mini-os-${SERVER_NAME}"; then
  echo "leftover libvirt domain detected for $SERVER_NAME" >&2
  exit 1
fi
if ovs-vsctl list-ports "${OVS_BRIDGE:-br-int}" 2>/dev/null | grep -q "^tap-mini-"; then
  echo "leftover tap-mini OVS port detected" >&2
  exit 1
fi
