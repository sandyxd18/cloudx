#!/usr/bin/env bash
# Prune old PostgreSQL backups in MinIO beyond the retention count for a tier.
# Usage: retention.sh <daily|weekly|monthly>
set -euo pipefail

TIER="${1:-daily}"
BUCKET="${BACKUP_BUCKET:-cloudx-backups}"
MINIO_ENDPOINT="${MINIO_ENDPOINT:-http://127.0.0.1:9000}"

case "$TIER" in
    daily)   KEEP="${BACKUP_RETENTION_DAILY:-7}" ;;
    weekly)  KEEP="${BACKUP_RETENTION_WEEKLY:-4}" ;;
    monthly) KEEP="${BACKUP_RETENTION_MONTHLY:-12}" ;;
    *) echo "unknown tier: $TIER" >&2; exit 1 ;;
esac

mc alias set cloudx "$MINIO_ENDPOINT" "${MINIO_ACCESS_KEY:-minioadmin}" "${MINIO_SECRET_KEY:-minioadmin}" >/dev/null

PREFIX="cloudx/${BUCKET}/postgres/${TIER}/"

# List objects (oldest first), drop the newest $KEEP, delete the rest.
mapfile -t OBJECTS < <(mc ls "$PREFIX" 2>/dev/null | awk '{print $NF}' | sort)
COUNT="${#OBJECTS[@]}"
if [ "$COUNT" -le "$KEEP" ]; then
    echo "  retention(${TIER}): ${COUNT} <= ${KEEP}, nothing to prune"
    exit 0
fi

DELETE=$((COUNT - KEEP))
echo "  retention(${TIER}): pruning ${DELETE} of ${COUNT} (keep ${KEEP})"
for ((i = 0; i < DELETE; i++)); do
    mc rm "${PREFIX}${OBJECTS[$i]}" || true
done
