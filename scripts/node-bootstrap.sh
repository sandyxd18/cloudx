#!/usr/bin/env bash
# node-bootstrap.sh — All-in-one node preparation for mini-OpenStack lab
# Run once on a fresh VM: sudo ./scripts/node-bootstrap.sh
# Safe to re-run (idempotent).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Require root ───────────────────────────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: Run with sudo: sudo $0" >&2
    exit 1
fi

# ── Load .env ──────────────────────────────────────────────────────────────────
ENV_FILE="$ROOT_DIR/.env"
if [ -f "$ENV_FILE" ]; then
    set -a; source "$ENV_FILE"; set +a
    echo "✓ Loaded configuration from .env"
fi

NETWORK_INTERFACE="${NETWORK_INTERFACE:-enp0s3}"
PROVIDER_INTERFACE="${PROVIDER_INTERFACE:-enp0s8}"
PROVIDER_BRIDGE="br-ex"
PROVIDER_PHYSNET="physnet1"
INTERNAL_VIP_ADDRESS="${INTERNAL_VIP_ADDRESS:-}"
PROVIDER_HOST_IP="${INTERNAL_VIP_ADDRESS}"
PROVIDER_HOST_IP_ADDR="${PROVIDER_HOST_IP%%/*}"   # strip CIDR notation
PROVIDER_GATEWAY="${PROVIDER_GATEWAY:-192.168.10.254}"  # OVN router external IP on br-ex

METADATA_IP="169.254.169.254"
OVN_SB_PORT="${OVN_SB_PORT:-6642}"

step() { echo ""; echo "══════ $* ══════"; }

# ── Step 1: Install packages ───────────────────────────────────────────────────
step "1/7  Installing packages"
export DEBIAN_FRONTEND=noninteractive
# -o Acquire::Check-Valid-Until=false tolerates VM clock drift after reboot
apt-get -o Acquire::Check-Valid-Until=false -qq update
apt-get -o Acquire::Check-Valid-Until=false install -y \
    qemu-kvm libvirt-daemon-system libvirt-clients virtinst \
    qemu-utils cloud-image-utils genisoimage \
    openvswitch-switch ovn-host \
    curl wget
echo "✓ Packages installed"

# ── Step 2: Enable system services ────────────────────────────────────────────
step "2/7  Enabling system services"
systemctl enable --now openvswitch-switch
systemctl enable --now libvirtd 2>/dev/null || \
    systemctl enable --now libvirt-daemon 2>/dev/null || true

# OVN Controller (managed by systemd on compute nodes)
systemctl enable --now ovn-controller.service 2>/dev/null || true
echo "✓ openvswitch-switch, libvirtd, and ovn-controller enabled"

# ── Step 2b: Kernel networking parameters ──────────────────────────────────────
# ip_nonlocal_bind  — required so HAProxy/Keepalived can bind to INTERNAL_VIP_ADDRESS
#                     even while the VIP is not active (Backup state) on this node.
# ip_forward        — required for routing between networks (VM ↔ provider network).
cat > /etc/sysctl.d/90-cloudx.conf << 'SYSCTL'
net.ipv4.ip_nonlocal_bind = 1
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
SYSCTL
sysctl -p /etc/sysctl.d/90-cloudx.conf > /dev/null
echo "✓ sysctl: ip_nonlocal_bind=1, ip_forward=1 (persisted to /etc/sysctl.d/90-cloudx.conf)"

# ── Step 3: User groups ────────────────────────────────────────────────────────
step "3/7  Configuring user groups"
if [ -n "${SUDO_USER:-}" ]; then
    usermod -aG libvirt,kvm "$SUDO_USER" || true
    echo "✓ User '$SUDO_USER' added to libvirt and kvm groups"
    echo ""
    echo "  ┌──────────────────────────────────────────────────────────┐"
    echo "  │  Run 'newgrp libvirt' after this script to activate the  │"
    echo "  │  group without logging out and back in.                  │"
    echo "  └──────────────────────────────────────────────────────────┘"
fi

# ── Step 4: Storage directories ────────────────────────────────────────────────
step "4/7  Creating storage directories"
mkdir -p \
    /var/lib/cloudx/compute/instances \
    /var/lib/cloudx/compute/cache/images \
    /var/lib/cloudx/volumes \
    /var/lib/cloudx/images
chown -R root:kvm /var/lib/cloudx 2>/dev/null || true
chmod -R g+rwX  /var/lib/cloudx 2>/dev/null || true
echo "✓ Storage directories ready at /var/lib/cloudx"

# ── Step 5: OVS bridges ────────────────────────────────────────────────────────
step "5/7  Configuring OVS bridges"

# br-int — managed by OVN controller (fail_mode=secure is required)
ovs-vsctl --may-exist add-br br-int
ovs-vsctl set bridge br-int \
    external_ids:cloudx=true \
    fail_mode=secure \
    datapath_type=system
echo "✓ br-int ready (fail_mode=secure)"

# br-ex — provider bridge
ovs-vsctl --may-exist add-br "$PROVIDER_BRIDGE"
ovs-vsctl set bridge "$PROVIDER_BRIDGE" external_ids:cloudx=true

# Attach provider interface if not already attached
if ovs-vsctl list-ports "$PROVIDER_BRIDGE" 2>/dev/null | grep -Fx "$PROVIDER_INTERFACE" >/dev/null 2>&1; then
    echo "✓ $PROVIDER_INTERFACE already attached to $PROVIDER_BRIDGE"
elif ip link show "$PROVIDER_INTERFACE" >/dev/null 2>&1; then
    ovs-vsctl --may-exist add-port "$PROVIDER_BRIDGE" "$PROVIDER_INTERFACE"
    echo "✓ $PROVIDER_INTERFACE → $PROVIDER_BRIDGE"
else
    echo "⚠ Interface $PROVIDER_INTERFACE not found — attach it manually later"
fi

ip link set "$PROVIDER_BRIDGE" up 2>/dev/null || true
ip link set "$PROVIDER_INTERFACE" up 2>/dev/null || true

# Patch port br-int ↔ br-ex
# The OVN controller container does not create this automatically because it does not run on the host network.
# This patch port must exist so provider network traffic can enter the OVN pipeline on br-int.
ovs-vsctl \
    -- --may-exist add-port br-int patch-br-int-to-br-ex \
    -- set interface patch-br-int-to-br-ex type=patch options:peer=patch-br-ex-to-br-int \
    -- --may-exist add-port "$PROVIDER_BRIDGE" patch-br-ex-to-br-int \
    -- set interface patch-br-ex-to-br-int type=patch options:peer=patch-br-int-to-br-ex
echo "✓ Patch ports: br-int ↔ $PROVIDER_BRIDGE"

# Assign host IP to br-ex if configured
if [ -n "$PROVIDER_HOST_IP" ]; then
    ip addr replace "$PROVIDER_HOST_IP" dev "$PROVIDER_BRIDGE" 2>/dev/null || \
    ip addr add    "$PROVIDER_HOST_IP" dev "$PROVIDER_BRIDGE" 2>/dev/null || true
    echo "✓ IP $PROVIDER_HOST_IP assigned to $PROVIDER_BRIDGE"
else
    echo "⚠ PROVIDER_HOST_IP not set — br-ex will have no IP (set it in .env)"
fi

# Metadata proxy setup:
# 169.254.169.254 on br-ex → iptables DNAT :80→PROVIDER_HOST_IP:8775 → metadata-api
# This lets cloud-init inside VMs reach the CloudX metadata API.
ip addr replace "$METADATA_IP/32" dev "$PROVIDER_BRIDGE" 2>/dev/null || \
ip addr add    "$METADATA_IP/32" dev "$PROVIDER_BRIDGE" 2>/dev/null || true
echo "✓ Metadata IP $METADATA_IP on $PROVIDER_BRIDGE"



# iptables DNAT: VM → 169.254.169.254:80 → PROVIDER_HOST_IP:8775
if [ -n "$PROVIDER_HOST_IP_ADDR" ] && command -v iptables >/dev/null 2>&1; then
    iptables -t nat -C PREROUTING -d "$METADATA_IP" -p tcp --dport 80 \
        -j DNAT --to-destination "${PROVIDER_HOST_IP_ADDR}:8775" 2>/dev/null || \
    iptables -t nat -A PREROUTING -d "$METADATA_IP" -p tcp --dport 80 \
        -j DNAT --to-destination "${PROVIDER_HOST_IP_ADDR}:8775"
    echo "✓ iptables: $METADATA_IP:80 → ${PROVIDER_HOST_IP_ADDR}:8775 (metadata-api)"
fi

# ── Step 6: Configure OVS for OVN ─────────────────────────────────────────────
step "6/7  Configuring host OVS for OVN"
OVN_REMOTE="tcp:127.0.0.1:${OVN_SB_PORT}"
ovs-vsctl set open . \
    external_ids:ovn-remote="$OVN_REMOTE" \
    external_ids:ovn-encap-type=geneve \
    external_ids:ovn-encap-ip=127.0.0.1 \
    external_ids:ovn-bridge-mappings="${PROVIDER_PHYSNET}:${PROVIDER_BRIDGE}"
echo "✓ Host OVS → OVN SB: $OVN_REMOTE"
echo "✓ Bridge mappings: ${PROVIDER_PHYSNET}:${PROVIDER_BRIDGE}"

# ── Step 7: Systemd service for br-ex IP persistence ──────────────────────────
step "7/7  Installing br-ex persistence service"
mkdir -p /etc/cloudx

# Write node config (read by the systemd service at boot)
cat > /etc/cloudx/node.env <<EOF
NETWORK_INTERFACE=${NETWORK_INTERFACE}
PROVIDER_BRIDGE=${PROVIDER_BRIDGE}
PROVIDER_INTERFACE=${PROVIDER_INTERFACE}
PROVIDER_HOST_IP=${PROVIDER_HOST_IP}
PROVIDER_HOST_IP_ADDR=${PROVIDER_HOST_IP_ADDR}
PROVIDER_GATEWAY=${PROVIDER_GATEWAY}
INTERNAL_VIP_ADDRESS=${INTERNAL_VIP_ADDRESS}

METADATA_IP=${METADATA_IP}
EOF

cat > /etc/systemd/system/cloudx-network.service <<'UNIT'
[Unit]
Description=mini-OpenStack Lab — Provider Bridge Network Setup
Documentation=https://github.com/your-org/cloudx
After=openvswitch-switch.service
Requires=openvswitch-switch.service
Before=libvirtd.service

[Service]
Type=oneshot
RemainAfterExit=yes
EnvironmentFile=/etc/cloudx/node.env
ExecStart=/bin/bash -c '\
    ip link set "${PROVIDER_BRIDGE:-br-ex}" up 2>/dev/null || true; \
    ip link set "${PROVIDER_INTERFACE:-enp0s8}" up 2>/dev/null || true; \
    if [ -n "${PROVIDER_HOST_IP:-}" ]; then \
        ip addr replace "${PROVIDER_HOST_IP}" dev "${PROVIDER_BRIDGE:-br-ex}" 2>/dev/null || \
        ip addr add    "${PROVIDER_HOST_IP}" dev "${PROVIDER_BRIDGE:-br-ex}" 2>/dev/null || true; \
    fi; \
    ip addr replace "${METADATA_IP:-169.254.169.254}/32" dev "${PROVIDER_BRIDGE:-br-ex}" 2>/dev/null || \
    ip addr add    "${METADATA_IP:-169.254.169.254}/32" dev "${PROVIDER_BRIDGE:-br-ex}" 2>/dev/null || true; \
    ovs-vsctl \
        -- --may-exist add-port br-int patch-br-int-to-br-ex \
        -- set interface patch-br-int-to-br-ex type=patch options:peer=patch-br-ex-to-br-int \
        -- --may-exist add-port "${PROVIDER_BRIDGE:-br-ex}" patch-br-ex-to-br-int \
        -- set interface patch-br-ex-to-br-int type=patch options:peer=patch-br-int-to-br-ex 2>/dev/null || true; \
    if [ -n "${PROVIDER_HOST_IP_ADDR:-}" ]; then \
        iptables -t nat -C PREROUTING -d "${METADATA_IP:-169.254.169.254}" -p tcp --dport 80 \
            -j DNAT --to-destination "${PROVIDER_HOST_IP_ADDR}:8775" 2>/dev/null || \
        iptables -t nat -A PREROUTING -d "${METADATA_IP:-169.254.169.254}" -p tcp --dport 80 \
            -j DNAT --to-destination "${PROVIDER_HOST_IP_ADDR}:8775"; \
    fi; \
    echo "cloudx-network: provider bridge and metadata proxy ready"'

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable cloudx-network.service
systemctl start  cloudx-network.service 2>/dev/null || true
echo "✓ cloudx-network.service installed, enabled, started"

# ── Final summary ──────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════"
echo "  Node bootstrap complete!"
echo "════════════════════════════════════════════════════════"
echo ""
echo "  KVM:    $([ -e /dev/kvm ] && echo '✓ /dev/kvm available' || echo '✗ /dev/kvm missing — enable nested virt')"
echo "  br-int: $(ovs-vsctl br-exists br-int && echo '✓ exists' || echo '✗ missing')"
echo "  br-ex:  $(ovs-vsctl br-exists "$PROVIDER_BRIDGE" && echo '✓ exists' || echo '✗ missing')"
echo "  IP:     $(ip addr show "$PROVIDER_BRIDGE" 2>/dev/null | grep -oP '(?<=inet )\S+' || echo 'none')"
echo ""
echo "  Next steps:"
echo "    1. newgrp libvirt        (activate group membership)"
echo "    2. make install-systemd && make start"
echo "    3. Configure the cloud resources manually (see README.md Step 4)"
echo ""
