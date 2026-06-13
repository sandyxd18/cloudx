#!/usr/bin/env bash
set -euo pipefail
compute host show | python3 -c 'import json,sys; data=json.load(sys.stdin); p=data["placement"]; assert "total_vcpu" in p and "used_ram_mb" in p and "free_disk_gb" in p'
echo "placement-lite smoke test passed"
