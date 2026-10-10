#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# CloudX Compute Worker Node Setup Script
# ==============================================================================
# Usage:
#   sudo CONTROLLER_IP=192.168.10.10 NODE_IP=192.168.10.11 PROVIDER_IF=enp0s8 ./scripts/setup-compute-node.sh
# ==============================================================================

# Parse CLI arguments if provided
while [[ $# -gt 0 ]]; do
    case "$1" in
        --central-ip|--controller-ip)
            CONTROLLER_IP="$2"
            shift 2
            ;;
        --encap-ip|--node-ip)
            NODE_IP="$2"
            shift 2
            ;;
        --node-name)
            NODE_NAME="$2"
            shift 2
            ;;
        --provider-interface|--provider-if)
            PROVIDER_IF="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

CONTROLLER_IP="${CONTROLLER_IP:-192.168.10.10}"
NODE_IP="${NODE_IP:-192.168.10.11}"
NODE_NAME="${NODE_NAME:-cloudx-compute-01}"
PROVIDER_IF="${PROVIDER_IF:-enp0s9}"

echo "======================================================================"
echo " CloudX Compute Node Setup: ${NODE_NAME}"
echo "======================================================================"
echo " Controller IP      : ${CONTROLLER_IP}"
echo " Node Management IP : ${NODE_IP}"
echo " Provider Interface : ${PROVIDER_IF}"
echo "======================================================================"

if [[ $EUID -ne 0 ]]; then
    echo "❌ Error: this script must be run as root / with sudo." >&2
    exit 1
fi

echo "===> [1/5] Installing KVM, libvirt, Open vSwitch, and OVN Host..."
apt-get update -qq
apt-get install -y -qq \
    qemu-kvm libvirt-daemon-system libvirt-clients \
    openvswitch-switch ovn-host qemu-utils qemu-guest-agent \
    genisoimage cloud-image-utils haproxy \
    libvirt-dev pkg-config build-essential python3-dev \
    python3-venv python3-pip curl jq bridge-utils >/dev/null
# genisoimage/cloud-image-utils build the cloud-init config drive; libvirt-python is what the compute worker drives
# libvirt with (without it the worker starts but cannot create VMs).
if [[ -x /opt/cloudx/venv/bin/pip ]]; then
    /opt/cloudx/venv/bin/pip install -q libvirt-python
fi

systemctl disable --now haproxy 2>/dev/null || true  # lb-worker runs its own haproxy per ALB
echo "===> [2/5] Ensuring libvirtd service is active..."
systemctl enable --now libvirtd || true
systemctl enable --now openvswitch-switch || true

echo "===> [3/5] Configuring OVN Controller target & Geneve encap..."
ovs-vsctl set open_vswitch . external_ids:ovn-remote="tcp:${CONTROLLER_IP}:6642"
ovs-vsctl set open_vswitch . external_ids:ovn-encap-type=geneve
ovs-vsctl set open_vswitch . external_ids:ovn-encap-ip="${NODE_IP}"
ovs-vsctl set open_vswitch . external_ids:system-id="${NODE_NAME}"
systemctl restart ovn-controller || systemctl restart openvswitch-switch

echo "===> [4/5] Setting up OVS Provider Bridge (br-ex)..."
if ! ovs-vsctl br-exists br-ex; then
    ovs-vsctl add-br br-ex
fi

if ip link show "${PROVIDER_IF}" >/dev/null 2>&1; then
    if ! ovs-vsctl list-ports br-ex | grep -q "^${PROVIDER_IF}$"; then
        echo "Binding ${PROVIDER_IF} to br-ex..."
        ovs-vsctl add-port br-ex "${PROVIDER_IF}" || true
    fi
    ip link set br-ex up
    ip link set br-ex promisc on
    ip link set "${PROVIDER_IF}" up
    ip link set "${PROVIDER_IF}" promisc on
else
    echo "⚠️ Warning: Provider interface '${PROVIDER_IF}' not found on this host. Skipping port binding."
fi

echo "===> [5/5] Generating Multi-Node Environment & Config Files..."
mkdir -p /var/lib/cloudx/compute

# /etc/cloudx/cloudx.conf (copied from the controller) holds the secrets; this node's services point at the controller.
# The compute worker names itself after this node (placement provider, server.host, its own RPC queue). Two compute
# nodes must never share a name, so it is set per node here.
for svc in compute-worker metadata-proxy telemetry-worker lb-worker; do
    mkdir -p "/etc/cloudx/${svc}"
    cat > "/etc/cloudx/${svc}/${svc}.conf" <<EOF
[DEFAULT]
compute_host = ${NODE_NAME}
node_name = ${NODE_NAME}
host_ip = ${NODE_IP}
rabbitmq_url = amqp://cloudx:cloudx@${CONTROLLER_IP}:5672/
database_url = postgresql+psycopg2://cloudx:cloudx@${CONTROLLER_IP}:5432/cloudx
auth_api_url = http://${CONTROLLER_IP}:8005
placement_api_url = http://${CONTROLLER_IP}:8006
networking_api_url = http://${CONTROLLER_IP}:8001
compute_api_url = http://${CONTROLLER_IP}:8003
image_api_url = http://${CONTROLLER_IP}:8002
telemetry_api_url = http://${CONTROLLER_IP}:8010
load_balancer_api_url = http://${CONTROLLER_IP}:8008
EOF
done
echo ""
echo "✓ Compute Node Setup Complete! (COMPUTE_HOST=${NODE_NAME})"
echo "Next Steps:"
echo "1. Install the CloudX services: sudo ./scripts/install-systemd.sh (see SETUP.md, section 4.4)"
echo "2. Start the worker: sudo systemctl enable --now cloudx-compute-worker cloudx-metadata-proxy"
echo "3. To serve LVM volumes from this node too: sudo ./scripts/prepare-lvm-node.sh --help"
