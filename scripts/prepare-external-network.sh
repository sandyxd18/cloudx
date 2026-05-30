#!/usr/bin/env bash
set -euo pipefail

echo "prepare-external-network.sh is kept for compatibility."
echo "Use scripts/bootstrap-provider-network.sh for provider bridge/NIC setup."
exec "$(dirname "$0")/bootstrap-provider-network.sh" "$@"
