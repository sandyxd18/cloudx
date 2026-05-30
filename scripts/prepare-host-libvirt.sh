#!/usr/bin/env bash
set -euo pipefail

CREATE_BR_INT=false
PROVIDER_CHECK=false
CONFIGURE_OVN=false
PROVIDER_INTERFACE="${PROVIDER_INTERFACE:-enp0s8}"
PROVIDER_BRIDGE="${PROVIDER_BRIDGE:-br-ex}"
PROVIDER_PHYSNET="${PROVIDER_PHYSNET:-physnet1}"
PROVIDER_IP="${PROVIDER_IP:-}"
OVN_SB_HOST="${OVN_SB_HOST:-127.0.0.1}"
OVN_SB_PORT="${OVN_SB_PORT:-6642}"

for arg in "$@"; do
  case "$arg" in
    --create-br-int) CREATE_BR_INT=true ;;
    --provider-check) PROVIDER_CHECK=true ;;
    --configure-ovn) CONFIGURE_OVN=true ;;
    -h|--help)
      cat <<'USAGE'
Usage: scripts/prepare-host-libvirt.sh [--create-br-int] [--configure-ovn] [--provider-check]

Prepare an Ubuntu/Debian single-node lab host for libvirt-backed VMs.
The script installs packages and performs checks. It only creates br-int
when --create-br-int is provided.
With --configure-ovn it configures host OVS to connect to OVN SB DB and
persists the br-ex IP via netplan so floating IPs survive reboots.
With --provider-check it also prints provider bridge/NIC readiness and the
bootstrap command to run explicitly.

Environment variables (override defaults):
  PROVIDER_INTERFACE  NIC to attach to br-ex (default: enp0s8)
  PROVIDER_BRIDGE     OVS external bridge (default: br-ex)
  PROVIDER_PHYSNET    OVN physical network name (default: physnet1)
  PROVIDER_IP         IP/prefix to assign to br-ex e.g. 192.168.10.102/24
  OVN_SB_HOST         OVN SB DB host (default: 127.0.0.1)
  OVN_SB_PORT         OVN SB DB port (default: 6642)
USAGE
      exit 0
      ;;
    *)
      echo "unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo: sudo scripts/prepare-host-libvirt.sh" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
# Use Check-Valid-Until=false to tolerate VM clock drift after resume/restart
apt-get -o Acquire::Check-Valid-Until=false update
apt-get -o Acquire::Check-Valid-Until=false install -y \
  qemu-kvm \
  libvirt-daemon-system \
  libvirt-clients \
  virtinst \
  qemu-utils \
  cloud-image-utils \
  genisoimage \
  openvswitch-switch \
  ovn-host

systemctl enable --now libvirtd || systemctl enable --now libvirt-daemon || true
systemctl enable --now openvswitch-switch || true

# Automatically add the invoking user to libvirt and kvm groups.
# NOTE: group membership only takes effect in NEW login sessions.
# After this script finishes, run: newgrp libvirt
if [ -n "${SUDO_USER:-}" ]; then
  usermod -aG libvirt,kvm "$SUDO_USER" || true
  echo "----------------------------------------------------------"
  echo "IMPORTANT: User '$SUDO_USER' added to libvirt and kvm groups."
  echo "Group membership is NOT active in the current shell session."
  echo "Run the following command to activate it without re-login:"
  echo ""
  echo "   newgrp libvirt"
  echo ""
  echo "Or log out and log back in for full effect."
  echo "----------------------------------------------------------"
fi

mkdir -p \
  /var/lib/mini-openstack/compute/instances \
  /var/lib/mini-openstack/compute/cache/images \
  /var/lib/mini-openstack/volumes
chown -R root:kvm /var/lib/mini-openstack || true
chmod -R g+rwX /var/lib/mini-openstack || true

if [ "$CREATE_BR_INT" = true ]; then
  ovs-vsctl --may-exist add-br br-int
  ovs-vsctl set bridge br-int external_ids:mini-openstack-lab=true
  ovs-vsctl set bridge br-int fail_mode=secure
fi

if [ "$CONFIGURE_OVN" = true ]; then
  echo "Configuring host OVS for OVN..."
  OVN_REMOTE="tcp:${OVN_SB_HOST}:${OVN_SB_PORT}"

  # Ensure br-ex exists and enp0s8 is attached
  ovs-vsctl --may-exist add-br "$PROVIDER_BRIDGE"
  ovs-vsctl set bridge "$PROVIDER_BRIDGE" external_ids:mini-openstack-lab=true
  if ! ovs-vsctl list-ports "$PROVIDER_BRIDGE" 2>/dev/null | grep -Fx "$PROVIDER_INTERFACE" > /dev/null 2>&1; then
    ovs-vsctl --may-exist add-port "$PROVIDER_BRIDGE" "$PROVIDER_INTERFACE" || true
  fi
  ip link set "$PROVIDER_BRIDGE" up || true
  ip link set "$PROVIDER_INTERFACE" up || true

  # Configure OVN connection on host OVS
  ovs-vsctl set open . \
    external_ids:ovn-remote="$OVN_REMOTE" \
    external_ids:ovn-encap-type=geneve \
    external_ids:ovn-encap-ip=127.0.0.1 \
    external_ids:ovn-bridge-mappings="${PROVIDER_PHYSNET}:${PROVIDER_BRIDGE}"
  echo "Host OVS configured: ovn-remote=$OVN_REMOTE"

  # Persist br-ex IP via netplan if PROVIDER_IP is given
  if [ -n "$PROVIDER_IP" ]; then
    NETPLAN_FILE="/etc/netplan/60-mini-openstack-br-ex.yaml"
    cat > "$NETPLAN_FILE" <<EOF
network:
  version: 2
  bridges:
    ${PROVIDER_BRIDGE}:
      addresses:
        - ${PROVIDER_IP}
      openvswitch: {}
EOF
    chmod 600 "$NETPLAN_FILE"
    netplan apply 2>/dev/null || true
    echo "br-ex IP persisted via netplan: $PROVIDER_IP"
  else
    echo "Tip: set PROVIDER_IP=192.168.x.x/24 to persist br-ex IP across reboots."
  fi
fi

echo "Host checks:"
if grep -E '(vmx|svm)' /proc/cpuinfo >/dev/null 2>&1; then
  echo "CPU virtualization flag present"
else
  echo "CPU virtualization flag not visible; enable nested virtualization in the outer hypervisor"
fi

if [ -e /dev/kvm ]; then
  ls -l /dev/kvm
else
  echo "missing /dev/kvm; enable KVM or nested virtualization before using libvirt backend"
fi

echo "libvirt:"
virsh list --all || true

echo "OVS:"
ovs-vsctl show || true
if ovs-vsctl br-exists br-int; then
  echo "br-int exists"
else
  echo "br-int does not exist. Re-run with --create-br-int if this lab host should create it."
fi

if [ "$PROVIDER_CHECK" = true ]; then
  PROVIDER_INTERFACE="${PROVIDER_INTERFACE:-enp0s8}"
  PROVIDER_BRIDGE="${PROVIDER_BRIDGE:-br-ex}"
  PROVIDER_PHYSNET="${PROVIDER_PHYSNET:-physnet1}"
  echo "Provider network:"
  if ip link show "$PROVIDER_INTERFACE" >/dev/null 2>&1; then
    echo "provider interface exists: $PROVIDER_INTERFACE"
  else
    echo "provider interface missing: $PROVIDER_INTERFACE"
  fi
  if ovs-vsctl br-exists "$PROVIDER_BRIDGE"; then
    echo "provider bridge exists: $PROVIDER_BRIDGE"
  else
    echo "provider bridge missing: $PROVIDER_BRIDGE"
  fi
  echo "ovn bridge mappings:"
  ovs-vsctl --if-exists get open . external_ids:ovn-bridge-mappings || true
  echo "To prepare provider bridge explicitly:"
  echo "  sudo PROVIDER_INTERFACE=$PROVIDER_INTERFACE PROVIDER_BRIDGE=$PROVIDER_BRIDGE PROVIDER_PHYSNET=$PROVIDER_PHYSNET scripts/bootstrap-provider-network.sh --dry-run"
fi

echo "User groups for the invoking user:"
if [ -n "${SUDO_USER:-}" ]; then
  id "$SUDO_USER" || true
fi

echo "Next steps:"
echo "  1. Ensure nested virtualization exposes /dev/kvm."
echo "  2. Bootstrap provider network:"
echo "     sudo PROVIDER_INTERFACE=$PROVIDER_INTERFACE PROVIDER_BRIDGE=$PROVIDER_BRIDGE scripts/bootstrap-provider-network.sh --force"
echo "  3. Configure OVN on host OVS (sets ovn-remote and persists br-ex IP):"
echo "     sudo PROVIDER_IP=<your-host-only-ip>/24 scripts/prepare-host-libvirt.sh --configure-ovn"
echo "  4. Run docker compose up -d --build."
echo "  5. Run compute host check."
