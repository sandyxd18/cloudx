#!/usr/bin/env bash
set -euo pipefail

PROVIDER_INTERFACE="${PROVIDER_INTERFACE:-enp0s8}"
PROVIDER_BRIDGE="${PROVIDER_BRIDGE:-br-ex}"
PROVIDER_PHYSNET="${PROVIDER_PHYSNET:-physnet1}"
DRY_RUN=true
FORCE=false
MOVE_IP=false

while [ "$#" -gt 0 ]; do
  case "$1" in
    --interface) PROVIDER_INTERFACE="$2"; shift 2 ;;
    --bridge) PROVIDER_BRIDGE="$2"; shift 2 ;;
    --physnet) PROVIDER_PHYSNET="$2"; shift 2 ;;
    --move-ip) MOVE_IP=true; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    --force) FORCE=true; DRY_RUN=false; shift ;;
    -h|--help)
      cat <<'USAGE'
Usage: scripts/bootstrap-provider-network.sh [--interface enp0s8] [--bridge br-ex] [--physnet physnet1] [--move-ip] [--dry-run] [--force]

Prepares the host provider bridge for floating IP experiments. Default mode is
dry-run. Attaching a real NIC or moving IP configuration can interrupt host
networking; use --force only from a safe console.
USAGE
      exit 0
      ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

run_or_print() {
  if [ "$DRY_RUN" = true ]; then
    echo " - $*"
  else
    "$@"
  fi
}

if ! has_cmd ovs-vsctl; then
  echo "ovs-vsctl is required" >&2
  exit 1
fi
if ! has_cmd ip; then
  echo "ip command is required" >&2
  exit 1
fi
if ! ip link show "$PROVIDER_INTERFACE" >/dev/null 2>&1; then
  echo "provider interface not found: $PROVIDER_INTERFACE" >&2
  exit 1
fi

echo "Provider network bootstrap summary:"
echo " interface: $PROVIDER_INTERFACE"
echo " bridge:    $PROVIDER_BRIDGE"
echo " physnet:   $PROVIDER_PHYSNET"
echo " move IP:   $MOVE_IP"

if [ "$DRY_RUN" = false ] && [ "$FORCE" != true ]; then
  echo "Refusing to apply without --force." >&2
  exit 2
fi

if [ "$DRY_RUN" = true ]; then
  echo "Planned changes:"
fi

run_or_print ovs-vsctl --may-exist add-br "$PROVIDER_BRIDGE"
run_or_print ovs-vsctl set bridge "$PROVIDER_BRIDGE" external_ids:cloudx=true

if ovs-vsctl list-ports "$PROVIDER_BRIDGE" 2>/dev/null | grep -Fx "$PROVIDER_INTERFACE" >/dev/null; then
  echo "provider interface already attached to $PROVIDER_BRIDGE"
else
  run_or_print ovs-vsctl --may-exist add-port "$PROVIDER_BRIDGE" "$PROVIDER_INTERFACE"
fi

run_or_print ovs-vsctl set open . external_ids:ovn-bridge-mappings="${PROVIDER_PHYSNET}:${PROVIDER_BRIDGE}"

if [ "$MOVE_IP" = true ]; then
  iface_cidrs="$(ip -o -4 addr show dev "$PROVIDER_INTERFACE" | awk '{print $4}' || true)"
  if [ -n "$iface_cidrs" ]; then
    while IFS= read -r cidr; do
      [ -z "$cidr" ] && continue
      run_or_print ip addr del "$cidr" dev "$PROVIDER_INTERFACE"
      run_or_print ip addr add "$cidr" dev "$PROVIDER_BRIDGE"
    done <<< "$iface_cidrs"
  else
    echo "no IPv4 address found on $PROVIDER_INTERFACE to move"
  fi
else
  echo "IP migration skipped. Use --move-ip --force only if this host design requires it."
fi

run_or_print ip link set "$PROVIDER_BRIDGE" up
run_or_print ip link set "$PROVIDER_INTERFACE" up

if [ "$DRY_RUN" = true ]; then
  echo "Dry-run mode: no changes made."
else
  echo "Provider bridge ready."
  ovs-vsctl show
fi
