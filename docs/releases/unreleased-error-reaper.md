# CloudX (version to be assigned) - error reaper and iSCSI cleanup

There are **upgrade steps**.

## Upgrading
1. Run `cloudx-db-migrate database container file_store` (adds `ready_at`; existing rows are set to `created_at`, so nothing that already exists is ever reaped).
2. Redeploy database-api, container-api, file-store-api and compute-worker.
3. On the controller install `cloudx-reaper.service` (runs as root, reads `auth_admin_password` from `cloudx.conf`) and optionally create `/etc/cloudx/reaper/reaper.conf` with `reaper_max_age_hours` (24), `reaper_interval` (600), `reaper_dry_run` (false). Enable it.

## Changes
- New `cloudx-reaper`: deletes Database instances, Containers, Shares and Share networks in `error` that never became ready and are older than the max age. It uses each service's public DELETE with an admin token, so downstream cleanup equals a user delete. Shares go before their share network.
- New `GET /internal/stale-errors?hours=N` on database-api, container-api and file-store-api.
- compute-worker: when it destroys a stale domain (VM evacuated elsewhere), it also logs out the iSCSI sessions of that domain's disks.

## Known limitations
- Only build failures are reaped; a resource that was ready once and later errored stays until deleted by hand.
- Failed Backups are not reaped.
- One reaper process on the controller; if it stops, nothing is cleaned (nothing breaks).
- A delete that keeps failing is retried every pass.
- iSCSI sessions are only cleaned for domains that compute-worker destroys as stale; orphan sessions with no domain are not.
