#!/usr/bin/env bash
#
# setup-host-provider-nat.sh
#
# Configures Linux Host networking for OpenStack Provider Network (vboxnet1):
# 1. Brings up vboxnet1 interface (IP 172.24.4.1/24)
# 2. Enables IPv4 forwarding
# 3. Sets up iptables MASQUERADE for 172.24.4.0/24 egress to internet
# 4. Ensures FORWARD rules allow traffic between vboxnet1 and internet interface
#

set -euo pipefail

PROVIDER_IF="${PROVIDER_IF:-vboxnet1}"
PROVIDER_CIDR="${PROVIDER_CIDR:-172.24.4.0/24}"
HOST_IP="${HOST_IP:-172.24.4.1}"

# Find default internet route interface on host
EXT_IF=$(ip -4 route show default | awk '{print $5}' | head -n1)

echo "=== [Host Provider NAT Setup] ==="
echo "Provider Interface : $PROVIDER_IF"
echo "Provider CIDR      : $PROVIDER_CIDR"
echo "Host Gateway IP    : $HOST_IP"
echo "Internet Interface : ${EXT_IF:-none}"

# 1. Bring up interface if down
if ip link show "$PROVIDER_IF" >/dev/null 2>&1; then
    sudo ip link set "$PROVIDER_IF" up
    sudo ip addr add "$HOST_IP/24" dev "$PROVIDER_IF" 2>/dev/null || true
else
    echo "Interface $PROVIDER_IF not found! Create it via VBoxManage hostonlyif create." >&2
    exit 1
fi

# 2. Enable IPv4 Forwarding
echo "Enabling net.ipv4.ip_forward..."
sudo sysctl -w net.ipv4.ip_forward=1 >/dev/null

# 3. Setup iptables rules
if [ -n "$EXT_IF" ]; then
    echo "Configuring iptables NAT (MASQUERADE) via $EXT_IF..."
    # NAT MASQUERADE
    if ! sudo iptables -t nat -C POSTROUTING -s "$PROVIDER_CIDR" -o "$EXT_IF" -j MASQUERADE 2>/dev/null; then
        sudo iptables -t nat -A POSTROUTING -s "$PROVIDER_CIDR" -o "$EXT_IF" -j MASQUERADE
    fi

    # FORWARD rules
    if ! sudo iptables -C FORWARD -i "$PROVIDER_IF" -o "$EXT_IF" -j ACCEPT 2>/dev/null; then
        sudo iptables -A FORWARD -i "$PROVIDER_IF" -o "$EXT_IF" -j ACCEPT
    fi
    if ! sudo iptables -C FORWARD -i "$EXT_IF" -o "$PROVIDER_IF" -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null; then
        sudo iptables -A FORWARD -i "$EXT_IF" -o "$PROVIDER_IF" -m state --state RELATED,ESTABLISHED -j ACCEPT
    fi
fi

# Allow forwarding within vboxnet1 and to host
if ! sudo iptables -C FORWARD -i "$PROVIDER_IF" -j ACCEPT 2>/dev/null; then
    sudo iptables -A FORWARD -i "$PROVIDER_IF" -j ACCEPT
fi

echo "=== Host Provider NAT setup complete ==="
