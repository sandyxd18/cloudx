#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Old per-service CLIs (networking/compute/block-store) are now one `cloudx`; scripts parse JSON.
cloudx() { command cloudx -o json "$@"; }
INTERNAL_TOKEN="${INTERNAL_TOKEN:-$(sudo grep -s '^INTERNAL_TOKEN=' /etc/cloudx/cloudx.env 2>/dev/null | cut -d= -f2 || true)}"

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
  cloudx server delete "$SERVER_NAME" >/dev/null 2>&1
  cloudx volume delete "${PREFIX}-vol" >/dev/null 2>&1
  cloudx floating-ip delete "${PREFIX}-fip" >/dev/null 2>&1
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

# Hypervisor checks run on the compute node: COMPUTE_SSH="ssh -J jump compute" when the CLI host is not the hypervisor.
hv() { if [ -n "${COMPUTE_SSH:-}" ]; then $COMPUTE_SSH "$@"; else "$@"; fi; }
hv test -e /dev/kvm || { echo "missing /dev/kvm; skipping real VM test" >&2; exit 1; }
hv virsh -c "${LIBVIRT_URI:-qemu:///system}" list --all >/dev/null
hv ${OVS_SUDO:-} ovs-vsctl br-exists "${OVS_BRIDGE:-br-int}"

cloudx network create "$NETWORK_NAME" || true
cloudx subnet create "$SUBNET_NAME" --network "$NETWORK_NAME" --cidr 10.42.0.0/24 --gateway 10.42.0.1 --dhcp || true
cloudx router create "$ROUTER_NAME" || true
cloudx router add-interface "$ROUTER_NAME" --subnet "$SUBNET_NAME" || true
cloudx image upload "$IMAGE_NAME" --file "$IMAGE_PATH" --disk-format qcow2 || true
cloudx keypair create "$KEY_NAME" || true
chmod 600 "${KEY_NAME}.pem" || true
cloudx server create "$SERVER_NAME" --image "$IMAGE_NAME" --flavor tiny --network "$NETWORK_NAME" --key-name "$KEY_NAME"

for _ in $(seq 1 30); do
  status="$(cloudx server show "$SERVER_NAME" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
  [ "$status" = "ACTIVE" ] && break
  [ "$status" = "ERROR" ] && { cloudx server show "$SERVER_NAME"; exit 1; }
  sleep 2
done

hv virsh -c "${LIBVIRT_URI:-qemu:///system}" list --all
hv ${OVS_SUDO:-} ovs-vsctl show
cloudx server ssh-info "$SERVER_NAME"
server_id="$(cloudx server show "$SERVER_NAME" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
curl -fsS -H "X-Internal-Token: ${INTERNAL_TOKEN:-}" "${METADATA_API_URL:-http://localhost:8775}/openstack/latest/meta_data.json?instance_id=${server_id}" >/dev/null
cloudx server delete "$SERVER_NAME" || true

# server delete is asynchronous (the worker tears the domain down): allow up to 60s
domains() { hv virsh -c "${LIBVIRT_URI:-qemu:///system}" list --all --name; }
for _ in $(seq 1 30); do [[ "$(domains)" == *"mini-os-${SERVER_NAME}"* ]] || break; sleep 2; done
if [[ "$(domains)" == *"mini-os-${SERVER_NAME}"* ]]; then
  echo "leftover libvirt domain detected for $SERVER_NAME" >&2
  exit 1
fi
if [[ "$(hv ${OVS_SUDO:-} ovs-vsctl list-ports "${OVS_BRIDGE:-br-int}" 2>/dev/null)" == *tap-mini-* ]]; then
  echo "leftover tap-mini OVS port detected" >&2
  exit 1
fi
echo "real VM smoke test passed"