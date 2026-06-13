#!/usr/bin/env bash
set -euo pipefail

METADATA_IP="${METADATA_IP:-169.254.169.254}"
METADATA_PROXY_PORT="${METADATA_PROXY_PORT:-8776}"

# OVN router's external IP (on br-ex / provider network).
# Reply packets from the host back to VMs must be routed via this gateway.
OVN_ROUTER_EXT_IP="${OVN_ROUTER_EXT_IP:-192.168.10.254}"

# Comma-separated list of private VM subnets to route via the OVN router.
# Add all tenant subnets here so the host can send replies back to VMs.
PRIVATE_SUBNETS="${PRIVATE_SUBNETS:-192.168.12.0/24}"

PROVIDER_BRIDGE="${PROVIDER_BRIDGE:-br-ex}"

DRY_RUN=true
FORCE=false

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    --force) FORCE=true; DRY_RUN=false; shift ;;
    -h|--help)
      cat <<USAGE
Usage: scripts/setup-metadata-route.sh [--dry-run] [--force]

Sets up host networking so VMs can reach the metadata service at ${METADATA_IP}:80.

Two things are required:
  1. iptables PREROUTING: redirect ${METADATA_IP}:80 → localhost:${METADATA_PROXY_PORT}
  2. Host route: <private-subnet> via <OVN-router-external-IP>
     Without this route, the host cannot send reply packets back to VMs.
     (The host would ARP for VM IPs on br-ex and get no reply.)

Environment variables:
  METADATA_IP           Metadata service IP (default: 169.254.169.254)
  METADATA_PROXY_PORT   Port of metadata-proxy (default: 8776)
  OVN_ROUTER_EXT_IP     OVN router external IP on provider bridge (default: 192.168.10.254)
  PRIVATE_SUBNETS       Comma-separated VM private subnets (default: 192.168.12.0/24)
  PROVIDER_BRIDGE       Provider OVS bridge connected to OVN router (default: br-ex)

Default is dry-run; pass --force to apply.
USAGE
      exit 0
      ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

ipt_rule=(iptables -t nat -A PREROUTING -d "$METADATA_IP/32" -p tcp --dport 80 -j REDIRECT --to-ports "$METADATA_PROXY_PORT")
ipt_check=(iptables -t nat -C PREROUTING -d "$METADATA_IP/32" -p tcp --dport 80 -j REDIRECT --to-ports "$METADATA_PROXY_PORT")

echo "Metadata route setup:"
echo "  [1] iptables PREROUTING: ${METADATA_IP}:80 → localhost:${METADATA_PROXY_PORT}"
echo "  [2] Host routes via OVN router (${OVN_ROUTER_EXT_IP} on ${PROVIDER_BRIDGE}):"
IFS=',' read -ra SUBNETS <<< "$PRIVATE_SUBNETS"
for subnet in "${SUBNETS[@]}"; do
  echo "        ${subnet} via ${OVN_ROUTER_EXT_IP}"
done

if [ "$DRY_RUN" = true ]; then
  echo ""
  echo "Dry-run mode: no changes made. Pass --force to apply."
  exit 0
fi

if [ "$FORCE" != true ]; then
  echo "Refusing to apply without --force." >&2
  exit 2
fi

# ── 1. iptables REDIRECT rule ──────────────────────────────────────────────────
if "${ipt_check[@]}" >/dev/null 2>&1; then
  echo "[1] iptables REDIRECT rule already exists — skipping"
else
  "${ipt_rule[@]}"
  echo "[1] iptables REDIRECT rule installed"
fi

# ── 2. Host routes for VM private subnets ─────────────────────────────────────
# Without these routes, the host cannot send TCP replies back to VMs.
# The OVN router at OVN_ROUTER_EXT_IP bridges the provider and private networks.
for subnet in "${SUBNETS[@]}"; do
  subnet="${subnet// /}"   # trim spaces
  if ip route show dev "$PROVIDER_BRIDGE" | grep -q "^${subnet}"; then
    echo "[2] Route ${subnet} via ${OVN_ROUTER_EXT_IP} already exists — skipping"
  else
    ip route add "$subnet" via "$OVN_ROUTER_EXT_IP" dev "$PROVIDER_BRIDGE"
    echo "[2] Route added: ${subnet} via ${OVN_ROUTER_EXT_IP} dev ${PROVIDER_BRIDGE}"
  fi
done

echo ""
echo "Done. Test with:"
echo "  curl http://${METADATA_IP}/latest/meta-data/instance-id"
