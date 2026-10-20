# CloudX (version to be assigned) - Database backup and restore

Backup and restore for VM + block databases. There are **upgrade steps**.

## Upgrading
1. Run `cloudx-db-migrate database` (`database_0002`, adds `db_backups`) and redeploy `database`, `shared`, `block-store-worker`, the dashboard and the CLI.
2. Restart `cloudx-block-store-worker` on every storage node.

## Changes
- `cloudx database backup create|list|restore|delete`, API `/instances/{id}/backups`, `/backups`, `/backups/{id}/restore`, and a Backups table in the Databases dashboard page.
- A restore always creates a new instance from the backup volume; the admin password is regenerated and shown once.
- **Behaviour change (all volume backups):** the block-store worker now backs up an LVM volume from a temporary snapshot instead of reading the live LV, so a volume in use no longer produces a torn copy. The snapshot is removed afterwards, also on failure.

## Known limitations
- Only `vm` + `block` (LVM). Container, local and file storage are rejected (422).
- Crash-consistent, not a logical dump: no per-database backup, no point-in-time recovery, no schedule or retention, no encryption of the backup object. Backups outlive their instance and must be deleted by hand.
- The engine major version must match: restore with the same Ubuntu image as the original.
- Restore always creates a new instance; there is no in-place restore.
- "Backup in use" protection is in memory and is lost when database-api restarts.
- OpenStack Trove stores backups in Swift and restores to a new instance; it uses a guest agent for logical backups, which this does not.
- Tested in the lab with an `mc` stand-in (no MinIO available there); the real MinIO path was not exercised.
- MariaDB restore resets `root@localhost` with `--skip-grant-tables --skip-networking` during first boot, because the backup carries the old root login.
