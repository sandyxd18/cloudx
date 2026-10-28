# Production readiness and multi-node deployment

Operations notes for multi-node deployment, high availability, backups, monitoring and internal TLS.

> CloudX runs natively under systemd, installed from `.deb` packages ([deb-install.md](deb-install.md)) or the tarball ([systemd-deployment-guide.md](systemd-deployment-guide.md)). This page says, feature by feature, what exists and what does not.

## 1. Status in the native deployment

| Feature | Status | Where |
|---|---|---|
| Multi-node (controller, compute, storage) | Supported | [deb-install.md](deb-install.md) sections 4 and 5, [multi-node-deployment.md](multi-node-deployment.md) |
| API load balancing across controllers | Not provided | put your own HAProxy or keepalived in front of the nginx on each controller |
| RabbitMQ cluster, PostgreSQL replication | Not provided | use the distro's own tooling |
| PostgreSQL backup | Not provided | schedule `pg_dump` yourself; see section 3 |
| Volume backup to object storage | Supported | block-store API, see section 3 |
| Metrics endpoint on every API | Supported | `/metrics`, bearer token `metrics_token` |
| Prometheus and Grafana | Not provided | scrape `/metrics` from your own Prometheus; see section 4 |
| Internal TLS between services | Not supported | no service terminates TLS |
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

**Database.** CloudX ships no backup job for its PostgreSQL databases. Run `pg_dump` (custom format) from cron or a systemd timer and keep the dumps off the host. Database instances created through the Database service have their own scheduled backups: [database.md](database.md).

**Volumes.** The block-store API backs up a volume to object storage:

```
POST   /volumes/{id}/backup      → full volume backup to object storage
GET    /backups                  → list backups
GET    /backups/{id}             → details
POST   /backups/{id}/restore     → restore into a new volume
DELETE /backups/{id}             → delete the backup + object
```

The worker exports the volume (LVM via `qemu-img`, Ceph via `rbd export`) and uploads it with `mc` to `cloudx-backups/volumes/`. This needs a MinIO (see [image-storage.md](image-storage.md)). Database instances use the same mechanism with a schedule: [database.md](database.md).

## 4. Monitoring

Every API exposes `/metrics` (from `shared/metrics.py`) with `cloudx_api_requests_total` and `cloudx_api_request_latency_seconds`. The endpoint answers 401 unless the request carries `Authorization: Bearer <metrics_token>`; [deb-install.md](deb-install.md) section 8 shows how to give Prometheus the token.

CloudX does not ship Prometheus, Grafana or alert rules. Add each API's `/metrics` as a scrape target in your own Prometheus (with the bearer token) and alert on `cloudx_api_requests_total` and the latency histogram as you see fit.

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
