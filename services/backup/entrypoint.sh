#!/usr/bin/env bash
# Backup runner entrypoint — installs the cron schedule and hands off to supercronic.
set -euo pipefail

BACKUP_CRON="${BACKUP_CRON:-0 23 * * *}"
export TZ="${TZ:-Asia/Jakarta}"

echo "CloudX backup runner"
echo "  Timezone : $TZ"
echo "  Schedule : $BACKUP_CRON  (min hour dom mon dow)"
echo "  Bucket   : ${BACKUP_BUCKET:-cloudx-backups}"

# Build the crontab consumed by supercronic.
CRONTAB=/tmp/cloudx.cron
echo "${BACKUP_CRON} /usr/local/bin/backup.sh >> /proc/1/fd/1 2>&1" > "$CRONTAB"

# Optionally run one backup immediately on startup for verification.
if [ "${BACKUP_RUN_ON_START:-false}" = "true" ]; then
    echo "BACKUP_RUN_ON_START=true → running an initial backup now..."
    /usr/local/bin/backup.sh || echo "initial backup failed (continuing to scheduler)"
fi

exec supercronic "$CRONTAB"
