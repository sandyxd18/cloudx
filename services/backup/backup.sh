#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# CloudX PostgreSQL backup → MinIO/S3
# ═══════════════════════════════════════════════════════════════════════════════
# Dumps the database with pg_dump (custom format, compressed) and uploads it to
# the MinIO backup bucket under a daily/weekly/monthly tier. Then prunes old
# snapshots per the retention policy.
#
# Env:
#   DATABASE_URL or PG* vars   — connection to the primary
#   MINIO_ENDPOINT / MINIO_ACCESS_KEY / MINIO_SECRET_KEY / BACKUP_BUCKET
#   BACKUP_RETENTION_DAILY / WEEKLY / MONTHLY
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

export TZ="${TZ:-Asia/Jakarta}"

PGHOST="${PGHOST:-127.0.0.1}"
PGPORT="${PGPORT:-5432}"
PGUSER="${POSTGRES_USER:-mini}"
PGDATABASE="${POSTGRES_DB:-mini_openstack}"
export PGPASSWORD="${POSTGRES_PASSWORD:-mini}"

BUCKET="${BACKUP_BUCKET:-cloudx-backups}"
MINIO_ENDPOINT="${MINIO_ENDPOINT:-http://127.0.0.1:9000}"

TS="$(date +%Y%m%d-%H%M%S)"
DOW="$(date +%u)"   # 1..7 (Mon..Sun)
DOM="$(date +%d)"   # 01..31

# Tier selection: monthly on the 1st, weekly on Sundays, otherwise daily.
if [ "$DOM" = "01" ]; then
    TIER="monthly"
elif [ "$DOW" = "7" ]; then
    TIER="weekly"
else
    TIER="daily"
fi

DUMP="/tmp/${PGDATABASE}-${TS}.dump"
OBJECT="postgres/${TIER}/${PGDATABASE}-${TS}.dump"

echo "[$(date)] Starting ${TIER} backup of ${PGDATABASE} @ ${PGHOST}:${PGPORT}"

# Wait for DB availability (in case runner starts before primary).
until pg_isready -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" >/dev/null 2>&1; do
    echo "  waiting for postgres..."
    sleep 3
done

pg_dump -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -Fc -f "$DUMP"
echo "  dump written: $(du -h "$DUMP" | cut -f1)"

# Configure mc alias and upload.
mc alias set cloudx "$MINIO_ENDPOINT" "${MINIO_ACCESS_KEY:-minioadmin}" "${MINIO_SECRET_KEY:-minioadmin}" >/dev/null
mc mb --ignore-existing "cloudx/${BUCKET}" >/dev/null
mc cp "$DUMP" "cloudx/${BUCKET}/${OBJECT}"
echo "  uploaded → ${BUCKET}/${OBJECT}"

rm -f "$DUMP"

# Prune old snapshots for this tier.
/usr/local/bin/retention.sh "$TIER"

echo "[$(date)] Backup complete."
