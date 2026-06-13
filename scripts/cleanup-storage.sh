#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=true
FORCE=false
PREFIX="test-mini-"
VOLUME_PATH="${VOLUME_STORE_PATH:-/var/lib/mini-openstack/volumes}"

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    --force) FORCE=true; DRY_RUN=false ;;
    -h|--help)
      cat <<'USAGE'
Usage: scripts/cleanup-storage.sh [--dry-run] [--force]

Deletes test-mini-* block-store volumes/snapshots through the CLI when
available, and prints orphan qcow2 candidates under VOLUME_STORE_PATH.
Default is dry-run.
USAGE
      exit 0
      ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

run_or_echo() {
  if [ "$DRY_RUN" = true ]; then
    echo "DRY-RUN: $*"
  else
    "$@" || true
  fi
}

if command -v block-store >/dev/null 2>&1; then
  snapshots="$(block-store snapshot list 2>/dev/null | python3 -c 'import json,sys; data=json.load(sys.stdin); print("\n".join(i["name"] for i in data.get("items", []) if i.get("name", "").startswith("test-mini-")))' || true)"
  while IFS= read -r item; do
    [ -z "$item" ] && continue
    run_or_echo block-store snapshot delete "$item"
  done <<< "$snapshots"

  volumes="$(block-store volume list 2>/dev/null | python3 -c 'import json,sys; data=json.load(sys.stdin); print("\n".join(i["name"] for i in data.get("items", []) if i.get("name", "").startswith("test-mini-")))' || true)"
  while IFS= read -r item; do
    [ -z "$item" ] && continue
    run_or_echo block-store volume force-detach "$item"
    run_or_echo block-store volume delete "$item"
  done <<< "$volumes"
else
  echo "block-store CLI not found; skipping API storage cleanup"
fi

if [ -d "$VOLUME_PATH" ]; then
  find "$VOLUME_PATH" -maxdepth 2 -type f \( -name 'test-mini-*.qcow2' -o -name '*test-mini*.qcow2' \) -print | while read -r file; do
    if [ "$DRY_RUN" = true ]; then
      echo "DRY-RUN: remove orphan $file"
    elif [ "$FORCE" = true ]; then
      rm -f "$file"
    fi
  done
fi
