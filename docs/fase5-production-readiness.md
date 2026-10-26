# Phase 5: Production Readiness & Multi-Node Deployment

Operations notes for the CloudX Phase 5 features: multi-node deployment, high availability, backups, monitoring and internal TLS.

> **What changed.** Phase 5 was first built as a Docker Compose deployment. Compose has been removed from the repository: CloudX now runs natively under systemd, installed from `.deb` packages ([deb-install.md](deb-install.md)) or the tarball ([systemd-deployment-guide.md](systemd-deployment-guide.md)). This page keeps the Phase 5 design and says, feature by feature, what exists in the native deployment and what does not.

## 1. Status in the native deployment

| Feature | Status | Where |
|---|---|---|
| Multi-node (controller, compute, storage) | Supported | [deb-install.md](deb-install.md) sections 4 and 5, [multi-node-deployment.md](multi-node-deployment.md) |
| API load balancing across controllers | Not packaged | `services/haproxy/` (config template) and `services/keepalived/` are reference material; no unit or package uses them |
| RabbitMQ cluster, PostgreSQL replication | Not packaged | use the distro's own tooling; `services/rabbitmq/` and `services/postgres/` hold the old container entrypoints and a `failover.sh` |
| PostgreSQL daily backup | Script only | `services/backup/` (`backup.sh`, `retention.sh`, `restore.sh`); see section 3 |
| Volume backup to object storage | Supported | block-store API, see section 3 |
| Metrics endpoint on every API | Supported | `/metrics`, bearer token `metrics_token` |
| Prometheus and Grafana | Reference configs only | `services/prometheus/`, `services/grafana/provisioning/` |
| Internal TLS between services | Not supported | `services/certs/generate-certs.sh` still generates a CA and certificates, but no service reads them |
| Database migrations | Supported | section 5 |

## 2. Node topology and principles

| Role | Components |
|---|---|
| **All-in-one** | every service on one host (see [quickstart.md](quickstart.md)) |
| **Control plane** | PostgreSQL, RabbitMQ, OVN central (NB/SB + northd), all APIs, dashboard |
| **Compute node** | `compute-worker` (libvirt), the other compute workers, local `ovn-controller` |
| **Storage node** | `block-store-worker` (LVM or Ceph) |

Storage principle: with Ceph RBD, compute nodes have no local VM disks. Root disks and volumes are served from the storage cluster over the network, and the compute node only keeps the libvirt XML and the config-drive ISO.

Network principle (OVN): the NB and SB databases and `ovn-northd` are centralised on the control plane. `ovn-controller` runs on every compute and gateway node and connects to the central SB DB at `tcp:<controller-ip>:6642`.

## 3. Backups

**Database.** `services/backup/backup.sh` dumps PostgreSQL with `pg_dump` (custom format) and uploads the dump to the MinIO bucket `cloudx-backups`, in daily, weekly (Sunday) and monthly (day 1) tiers. `retention.sh` prunes old dumps (defaults: 7 daily, 4 weekly, 12 monthly). `restore.sh` restores the latest daily dump, or a dump you name. The scripts read their settings from the environment (`DATABASE_URL` or the `PG*` variables, `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `BACKUP_BUCKET`, `BACKUP_RETENTION_*`, `TZ`). There is no unit or timer for them: schedule `backup.sh` from cron or a systemd timer yourself (23:00 Asia/Jakarta was the old default).

**Volumes.** The block-store API backs up a volume to object storage:

```
POST   /volumes/{id}/backup      → full volume backup to object storage
GET    /backups                  → list backups
GET    /backups/{id}             → details
POST   /backups/{id}/restore     → restore into a new volume
DELETE /backups/{id}             → delete the backup + object
```

The worker exports the volume (LVM via `qemu-img`, Ceph via `rbd export`) and uploads it with `mc` to `cloudx-backups/volumes/`. This needs a MinIO (see [storage-backend.md](storage-backend.md)). Database instances use the same mechanism with a schedule: [database.md](database.md).

## 4. Monitoring

Every API exposes `/metrics` (from `shared/metrics.py`) with `cloudx_api_requests_total` and `cloudx_api_request_latency_seconds`. The endpoint answers 401 unless the request carries `Authorization: Bearer <metrics_token>`; [deb-install.md](deb-install.md) section 8 shows how to give Prometheus the token.

`services/prometheus/prometheus.yml` and `services/prometheus/alerts.yml` are a starting point for your own Prometheus: the alert rules cover API, node and database down, P95 latency above 500 ms, a 5xx rate above 5%, and replication lag above 1 GB. `services/grafana/provisioning/` holds a datasource and two dashboards (Overview, Infrastructure). Installing and running Prometheus, Grafana and the exporters is up to you.

## 5. Database migrations (Alembic)

Alembic is the only path that creates or changes tables. Each service has its own migration history (`services/<service>/migrations/versions/`) and its own version table (`alembic_version_<service>`), so services may share one database or use separate ones. Services refuse to start if the schema has not been migrated. Details in `services/migrations/README.md`.

```bash
sudo cloudx-manage db sync            # packages: create/upgrade the schema of all services (idempotent)
sudo make migrate                     # tarball install: same thing
cloudx-db-migrate compute auth        # only specific services
cloudx-db-migrate --current           # current revision per service

# create a new migration after changing a model (additive-first, zero-downtime)
cloudx-db-migrate --revision compute -m "description" --autogenerate
```

Zero-downtime rule: add a nullable/default column first, deploy the code, backfill, and only then add constraints or drop the old column (in a separate migration).
