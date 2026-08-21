#!/usr/bin/env bash
# Manually promote this standby to primary (lab failover).
# Run inside the standby container: docker compose exec postgres-standby failover.sh
set -euo pipefail
PGDATA="${PGDATA:-/var/lib/postgresql/data}"
echo "Promoting standby at $PGDATA to primary..."
pg_ctl promote -D "$PGDATA"
echo "Promotion triggered. Verify with: psql -c 'SELECT pg_is_in_recovery();' (expect f)"
