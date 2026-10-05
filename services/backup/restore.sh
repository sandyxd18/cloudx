#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# CloudX PostgreSQL restore ← MinIO/S3
# ═══════════════════════════════════════════════════════════════════════════════
# Usage:
#   restore.sh                       # restore the newest daily backup
#   restore.sh postgres/daily/xxx.dump   # restore a specific object
#
# WARNING: this drops and recreates the target database. Only run against a DB
# you intend to overwrite.
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

PGHOST="${PGHOST:-127.0.0.1}"
PGPORT="${PGPORT:-5432}"
PGUSER="${POSTGRES_USER:-mini}"
PGDATABASE="${POSTGRES_DB:-cloudx}"
export PGPASSWORD="${POSTGRES_PASSWORD:-mini}"

BUCKET="${BACKUP_BUCKET:-cloudx-backups}"
MINIO_ENDPOINT="${MINIO_ENDPOINT:-http://127.0.0.1:9000}"

mc alias set cloudx "$MINIO_ENDPOINT" "${MINIO_ACCESS_KEY:-minioadmin}" "${MINIO_SECRET_KEY:-minioadmin}" >/dev/null

OBJECT="${1:-}"
if [ -z "$OBJECT" ]; then
    OBJECT="postgres/daily/$(mc ls "cloudx/${BUCKET}/postgres/daily/" | awk '{print $NF}' | sort | tail -1)"
    echo "No object given; using newest daily: $OBJECT"
fi

DUMP="/tmp/restore-$(basename "$OBJECT")"
mc cp "cloudx/${BUCKET}/${OBJECT}" "$DUMP"
echo "Downloaded $OBJECT"

echo "Restoring into ${PGDATABASE} @ ${PGHOST}:${PGPORT} (will --clean existing objects)"
pg_restore -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" --clean --if-exists "$DUMP"

rm -f "$DUMP"
echo "Restore complete."
