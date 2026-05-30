#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root_dir"

if [ "$(id -u)" -eq 0 ]; then
  ./scripts/cleanup-all.sh --force || true
else
  echo "Skipping host compute/network cleanup because reset is not running as root."
  echo "Run sudo scripts/cleanup-compute.sh --force and sudo scripts/cleanup-network.sh --force if host leftovers exist."
fi

docker compose down -v --remove-orphans
echo "Removed containers and lab volumes."
