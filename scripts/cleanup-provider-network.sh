#!/usr/bin/env bash
set -euo pipefail

PROVIDER_INTERFACE="${PROVIDER_INTERFACE:-enp0s8}"
PROVIDER_BRIDGE="${PROVIDER_BRIDGE:-br-ex}"
PROVIDER_PHYSNET="${PROVIDER_PHYSNET:-physnet1}"
DRY_RUN=true
FORCE=false
REMOVE_PROVIDER_BRIDGE=false
DETACH_PROVIDER_INTERFACE=false

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    --force) FORCE=true; DRY_RUN=false; shift ;;
    --remove-provider-bridge) REMOVE_PROVIDER_BRIDGE=true; shift ;;
    --detach-provider-interface) DETACH_PROVIDER_INTERFACE=true; shift ;;
    -h|--help)
      cat <<'USAGE'
Usage: scripts/cleanup-provider-network.sh [--dry-run] [--force] [--remove-provider-bridge] [--detach-provider-interface]

Conservative provider cleanup. Default is dry-run. It clears the OVN bridge
mapping by default, but only removes br-ex or detaches the provider NIC when
explicit flags are provided.
USAGE
      exit 0
      ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

run_or_print() {
  if [ "$DRY_RUN" = true ]; then
    echo " - $*"
  else
    "$@"
  fi
}

if [ "$DRY_RUN" = false ] && [ "$FORCE" != true ]; then
  echo "Refusing to apply without --force." >&2
  exit 2
fi

echo "Provider cleanup plan:"
run_or_print ovs-vsctl remove open . external_ids ovn-bridge-mappings="${PROVIDER_PHYSNET}:${PROVIDER_BRIDGE}"

if [ "$DETACH_PROVIDER_INTERFACE" = true ]; then
  run_or_print ovs-vsctl --if-exists del-port "$PROVIDER_BRIDGE" "$PROVIDER_INTERFACE"
else
  echo "provider interface detach skipped; use --detach-provider-interface --force"
fi

if [ "$REMOVE_PROVIDER_BRIDGE" = true ]; then
  run_or_print ovs-vsctl --if-exists del-br "$PROVIDER_BRIDGE"
else
  echo "provider bridge removal skipped; use --remove-provider-bridge --force"
fi

if [ "$DRY_RUN" = true ]; then
  echo "Dry-run mode: no changes made."
fi
