#!/usr/bin/env bash
set -euo pipefail

METADATA_IP="${METADATA_IP:-169.254.169.254}"
METADATA_PROXY_PORT="${METADATA_PROXY_PORT:-8776}"
DRY_RUN=true
FORCE=false

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    --force) FORCE=true; DRY_RUN=false; shift ;;
    -h|--help)
      cat <<USAGE
Usage: scripts/cleanup-metadata-route.sh [--dry-run] [--force]

Removes the metadata iptables redirect for ${METADATA_IP}:80.
USAGE
      exit 0
      ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

rule=(iptables -t nat -D PREROUTING -d "$METADATA_IP/32" -p tcp --dport 80 -j REDIRECT --to-ports "$METADATA_PROXY_PORT")
check=(iptables -t nat -C PREROUTING -d "$METADATA_IP/32" -p tcp --dport 80 -j REDIRECT --to-ports "$METADATA_PROXY_PORT")

if [ "$DRY_RUN" = true ]; then
  echo " - ${rule[*]}"
  echo "Dry-run mode: no changes made."
  exit 0
fi

if [ "$FORCE" != true ]; then
  echo "Refusing to apply without --force." >&2
  exit 2
fi

if "${check[@]}" >/dev/null 2>&1; then
  "${rule[@]}"
  echo "metadata route removed"
else
  echo "metadata route not present"
fi
