# Database as a Service (Trove-like)

PostgreSQL and MariaDB as a VM or a container, created with one call. It covers the basic Trove features, not 1:1.

## Architecture

- `database-api` (controller, port 8019): table `db_instances`. No new worker; the service calls compute, block-store, file-store, container and networking through their APIs with the caller's token.
- Create and delete run in a background thread. Statuses: `building`, `active`, `stopped`, `deleting`, `error`. A `building`/`deleting` row left behind when the API restarts is marked `error` (delete it again to clean up).
- Every instance gets its own security group: ingress on the engine port from `allowed_cidr` (default: the CIDR of the network's first subnet), egress allow-all. `0.0.0.0/0`, IPv6 and prefixes wider than /8 are rejected.
- The admin password is randomised when not given (`A-Za-z0-9_-`, 12-64 characters), returned once at create, and not stored.

## Matrix

| backend | local | block | file |
|---|---|---|---|
| vm + postgresql | yes | yes | rejected (422) |
| vm + mariadb | yes | yes | yes, not recommended |
| container | yes (Docker volume `ct-<id>-data`) | rejected | rejected |

Postgres on NFS failed at `pg_createcluster` (root squash) in the lab, so that combination is rejected.

## Readiness

- VM: cloud-init calls `POST {database_public_url}/internal/db-ready/<id>` with a single-use `X-Ready-Token` header (only its sha256 is stored). Limit 900 seconds.
- Container: the log is watched until the ready line appears after the init-finished line. Limit 300 seconds.

## Configuration

`cloudx.conf`: `database_api_url`, `database_public_url`. The second must be reachable from the tenant network (in the lab: `http://172.20.0.118:8019`). Make sure the file ends with a newline before adding lines.

## CLI

```
cloudx database create pg --engine postgresql --backend vm --network <net> --image <img> --flavor small --storage block --size 5
cloudx database list | show | start | stop | delete
```

## Backup and restore

VM + block instances only. A backup is a crash-consistent copy of the data volume: block-store-worker takes an LV snapshot, exports it to the backup bucket (MinIO), then deletes the snapshot. PostgreSQL and MariaDB recover from such a copy through the WAL or redo log, as after a power loss. This applies to all block-store volume backups, not only databases.

```
cloudx database backup create <instance> [--name N]
cloudx database backup list
cloudx database backup restore <backup> <new-name> --network <net> --image <img> --flavor <f>
cloudx database backup delete <backup>
```

- A restore always creates a new instance (volume from the backup, new VM, new security group). A new admin password is randomised and shown once; the old password is no longer used (`ALTER USER` at boot).
- Use the same Ubuntu image as the source instance: the engine's major version must match.
- A backup stays after the instance is deleted and must be deleted separately. A backup that a restore is using cannot be deleted (409; the guard is in memory only and is lost when database-api restarts).
- Container, local storage and file are rejected (422).

For comparison: Trove stores backups in Swift and a restore creates a new instance (docs.openstack.org/trove, "Backup and restore a database"). The difference: a backup here is a volume copy, not a logical dump taken through a guest agent.

### Schedule and retention

```
cloudx database backup schedule <instance> --every 6 --keep 7
cloudx database backup schedule <instance> --off
```

`cloudx-reaper` runs it (one cycle every `reaper_interval`, so precision is about 10 minutes): an `active` instance whose last scheduled backup is older than `--every` hours (or has none) gets a new backup. Then `available` scheduled backups beyond the newest `--keep` are deleted. Manual backups and `error` backups are never deleted automatically. Schedules are for VM + block only; `--every` is at least 1. If the reaper is down there are no scheduled backups and no retention.

## Automatic cleanup

The `cloudx-reaper` unit (controller, root) deletes `error` instances that failed during build (never had a `ready_at`) and are older than `reaper_max_age_hours` (24) in `/etc/cloudx/reaper/reaper.conf`. Instances that were once active and then failed are left alone. It also covers Container and File Store. `reaper_dry_run = true` only writes to the log. `error` backups are not cleaned up.

## Proven in the lab

container + postgres/mariadb (connection, wrong password rejected, data survives stop/start, clean delete); vm + postgres + block (connection, data); vm + mariadb + file (port open, auth rejected, SSH closed by the security group). Deleting leaves no volume, share or security group behind.

Backup and restore, VM + block: PostgreSQL 16 and MariaDB 10.11, with the backup taken while a client kept writing; the restore into a new instance carried the initial 1000 rows plus the rows that arrived during the backup, the old password was rejected and the new one worked, the source kept running, and no LV snapshot was left. The lab has no MinIO, so `mc` was temporarily replaced by an HTTP stub; the real MinIO path was not tested here.
