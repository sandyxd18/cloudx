# CloudX (version to be assigned) - Database service

New `database-api` (port 8019): PostgreSQL and MariaDB on a VM or in a container (Trove-like, not 1:1).

## Highlights

- Create, list, show, start, stop and delete through API, CLI (`cloudx database`) and dashboard (Databases).
- VM backend: local, block or file storage. Container backend: local storage only (Docker volume, removed with the container).
- Per-instance security group, ingress only from `allowed_cidr` (default: the network's first subnet). Wide or open CIDRs are rejected.
- Random admin password, returned once and never stored.
- Container worker: `data_path` volume support, `docker rm -v`, and logs now include stderr.

## Upgrading

1. Deploy, run `cloudx-db-migrate container` and `cloudx-db-migrate database` on the controller.
2. Copy and enable `cloudx-database-api`. Add `database_api_url` and `database_public_url` to `cloudx.conf`; the latter must be reachable from tenant VMs.
3. Redeploy `container-worker` on nodes that run containers.

## Limitations

- Not implemented: backup and restore, in-instance database and user management, replication, resize, upgrade, parameter groups, container with block or file storage.
- PostgreSQL on file (NFS) storage is rejected: it failed on NFS root squash in the lab. MariaDB on NFS works but is not recommended.
- The admin password sits in cloud-init user-data (VM) or the container environment.
- Container local data is lost if the node is lost.
- Start/stop only changes the recorded status of a VM instance; no polling.
- Creating two containers at the same moment can hit a duplicate-IP race in networking-api (pre-existing); create them one at a time.
- Interrupted create/delete leaves an `error` row; delete it again to clean up.
- Needs an Ubuntu cloud image with internet access for package install.
