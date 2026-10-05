#!/usr/bin/env bash
set -euo pipefail

# ── Validate required variables ───────────────────────────────────────────────
NETWORK_INTERFACE="${NETWORK_INTERFACE:-}"
INTERNAL_VIP_ADDRESS="${INTERNAL_VIP_ADDRESS:-}"

if [ -z "$NETWORK_INTERFACE" ]; then
    echo "ERROR: NETWORK_INTERFACE is not set. Keepalived requires a management interface." >&2
    exit 1
fi

if [ -z "$INTERNAL_VIP_ADDRESS" ]; then
    echo "ERROR: INTERNAL_VIP_ADDRESS is not set. Cannot start Keepalived without a VIP." >&2
    exit 1
fi

# Strip CIDR if only IP was provided — keepalived needs IP/PREFIX format
if [[ "$INTERNAL_VIP_ADDRESS" != */* ]]; then
    echo "WARNING: INTERNAL_VIP_ADDRESS has no prefix (e.g. /24). Appending /32 as fallback." >&2
    INTERNAL_VIP_ADDRESS="${INTERNAL_VIP_ADDRESS}/32"
fi

export NETWORK_INTERFACE
export INTERNAL_VIP_ADDRESS
export KEEPALIVED_STATE="${KEEPALIVED_STATE:-MASTER}"
export KEEPALIVED_PRIORITY="${KEEPALIVED_PRIORITY:-100}"
export KEEPALIVED_ROUTER_ID="${KEEPALIVED_ROUTER_ID:-51}"
export KEEPALIVED_AUTH_PASS="${KEEPALIVED_AUTH_PASS:-cloudx01}"
export HAPROXY_STATS_PORT="${HAPROXY_STATS_PORT:-8404}"

echo "Keepalived configuration:"
echo "  Interface : $NETWORK_INTERFACE"
echo "  VIP       : $INTERNAL_VIP_ADDRESS"
echo "  State     : $KEEPALIVED_STATE"
echo "  Priority  : $KEEPALIVED_PRIORITY"
echo "  Router ID : $KEEPALIVED_ROUTER_ID"

# Generate keepalived.conf from template
envsubst < /etc/keepalived/keepalived.conf.template > /etc/keepalived/keepalived.conf

echo ""
echo "Generated /etc/keepalived/keepalived.conf:"
cat /etc/keepalived/keepalived.conf
echo ""

echo "Starting Keepalived..."
exec keepalived --dont-fork --log-console --log-detail -f /etc/keepalived/keepalived.conf
