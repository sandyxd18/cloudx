#!/usr/bin/env bash
set -euo pipefail

# Old per-service CLIs (networking/compute/block-store) are now one `cloudx`; scripts parse JSON.
cloudx() { command cloudx -o json "$@"; }
INTERNAL_TOKEN="${INTERNAL_TOKEN:-$(sudo grep -s '^internal_token' /etc/cloudx/cloudx.conf 2>/dev/null | cut -d= -f2- | tr -d ' ' || true)}"
cloudx host show | python3 -c 'import json,sys; data=json.load(sys.stdin); p=data["placement"]; assert "total_vcpu" in p and "used_ram_mb" in p and "free_disk_gb" in p'
echo "placement-lite smoke test passed"
