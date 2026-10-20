# CloudX (version to be assigned) - scheduled database backups

There are **upgrade steps**.

## Upgrading
1. Run `cloudx-db-migrate database` (`database_0004`: `db_instances.backup_every_hours`, `backup_keep`; `db_backups.scheduled`).
2. Redeploy database-api, cloudx-reaper and the CLI. The reaper must be enabled (see the error reaper notes).

## Changes
- `PUT /instances/{id}/backup-schedule` `{every_hours, keep}` (both null turns it off); `cloudx database backup schedule <instance> --every H --keep N | --off`.
- cloudx-reaper creates due backups and deletes scheduled backups beyond `keep`. Manual and failed backups are never deleted automatically.
- `GET /internal/backup-due`, `GET /internal/backup-expired`; backups carry `scheduled`.

## Known limitations
- Precision is about one reaper interval (default 10 min); no fixed time of day or window.
- If the reaper is stopped there are no scheduled backups and no retention.
- VM + block only. Retention counts backups, not age or size.
- Failed scheduled backups stay until deleted by hand and do not block the next one.
- Real MinIO is still untested (same as manual backups). The dashboard has no schedule control.
