#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=false
FORCE=false
WITH_VOLUMES=false

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    --force) FORCE=true ;;
    --with-volumes) WITH_VOLUMES=true ;;
    -h|--help)
      cat <<'USAGE'
Usage: scripts/cleanup-all.sh [--dry-run] [--force] [--with-volumes]

Runs compute and network cleanup. With --with-volumes, also runs
docker compose down -v --remove-orphans after host resource cleanup.
USAGE
      exit 0
      ;;
    *)
      echo "unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root_dir"

args=()
[ "$DRY_RUN" = true ] && args+=(--dry-run)
[ "$FORCE" = true ] && args+=(--force)

./scripts/cleanup-metadata-route.sh "${args[@]}"
./scripts/cleanup-storage.sh "${args[@]}"
./scripts/cleanup-compute.sh "${args[@]}"
./scripts/cleanup-network.sh "${args[@]}"

if [ "$WITH_VOLUMES" = true ]; then
  if [ "$DRY_RUN" = true ]; then
    echo "Dry-run mode: would run docker compose down -v --remove-orphans"
  else
    docker compose down -v --remove-orphans
  fi
fi
