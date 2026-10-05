# 🚀 Phase 5 — Production Readiness & Multi-Node Deployment

Operations guide for the CloudX Phase 5 features: adaptive multi-node deployment,
High Availability, automatic backups, monitoring, and internal TLS.

> **Legacy note.** The Docker Compose deployment described in sections 1-6 has been removed from the repository
> (CloudX now runs natively under systemd; see `systemd-deployment-guide.md` and `multi-node-deployment.md`). The
> `docker compose` commands and compose file names below are kept only as a description of the original design.

Every feature is **adaptive**: it can run single-node (all-in-one) for
dev/lab, or be split across several nodes for pseudo-production.

---

## 1. Node Topology

| Role | Compose file | Components |
|------|-------------|----------|
| **All-in-One** (single node) | `docker-compose.yml` | All services on one host |
| **Control Plane** | `docker-compose.control-plane.yml` | PostgreSQL, RabbitMQ, OVN NB/SB+northd, all APIs, dashboard, HAProxy, Keepalived |
| **Compute Node** | `docker-compose.compute-node.yml` | compute-worker (libvirt) + local ovn-controller |
| **Storage Node** | `docker-compose.storage-node.yml` | block-store-worker (LVM/Ceph) + MinIO |

Key variables in `.env`:

```bash
TIMEZONE=Asia/Jakarta            # timezone of all containers (logs, cron, backup)
CONTROLLER_IP=10.0.0.11          # control plane management IP (used by compute/storage nodes)
NETWORK_INTERFACE=enp0s3         # management NIC → OVN Geneve tunnel
PROVIDER_INTERFACE=enp0s8        # provider NIC → br-ex (gateway node)
```

### Storage principle
Compute nodes have **no** local VM disks. VM root disks & volumes are served from the
**Storage Node** (Ceph RBD) over the management network. The compute node only
keeps the libvirt XML + config-drive ISO, ephemerally.

### Network principle (OVN, like CloudX)
- `ovn-nb-db`, `ovn-sb-db`, `ovn-northd` are **centralised** on the control plane.
- `ovn-controller` runs on **every** compute/gateway node, connected to the central
  SB DB via `tcp:${CONTROLLER_IP}:6642`.

---

## 2. Multi-Node Deployment

### 2.1 Control plane (controller node)
```bash
cp .env.example .env
# set CONTROLLER_IP, NETWORK_INTERFACE, secrets
docker compose -f docker-compose.control-plane.yml up -d
```

### 2.2 Storage node
```bash
# .env: CONTROLLER_IP=<controller ip>, LVM_PV_NAME / CEPH_POOL
docker compose -f docker-compose.storage-node.yml up -d
```

### 2.3 Compute node
```bash
# .env: CONTROLLER_IP=<controller ip>, NETWORK_INTERFACE
docker compose -f docker-compose.compute-node.yml up -d
```

### 2.4 Single-node (adaptive)
```bash
docker compose up -d          # all roles on one host — nothing changes
```

---

## 3. High Availability

### 3.1 HAProxy multi-backend
HAProxy load-balances the APIs across several instances. Set the backend list in `.env`:

```bash
AUTH_BACKENDS=10.0.0.11,10.0.0.12,10.0.0.13
COMPUTE_BACKENDS=10.0.0.11,10.0.0.12,10.0.0.13
# and so on for NETWORKING/IMAGE/BLOCK_STORE/PLACEMENT/DASHBOARD
```
A single controller can simply keep `127.0.0.1` (the default). Round-robin and the
`/health` health check are enabled automatically. Stats: `http://<node>:8404/stats`,
Prometheus metrics: `http://<node>:8404/metrics`.

### 3.2 RabbitMQ cluster (3 nodes)
```bash
docker compose -f docker-compose.control-plane.yml -f docker-compose.ha.yml up -d
```
- The same Erlang cookie on all nodes (`RABBITMQ_ERLANG_COOKIE`).
- Non-seed nodes automatically `join_cluster` to `RABBITMQ_SEED_NODE`.
- The `ha-all` policy mirrors all classic queues to every node.

### 3.3 PostgreSQL streaming replication
- `POSTGRES_ROLE=primary` → creates the replication role + `pg_hba` entries for the standby.
- `POSTGRES_ROLE=standby` → `pg_basebackup` from `POSTGRES_PRIMARY_HOST`, then
  runs as a read-only hot standby.
- Manual failover (lab): `docker compose exec postgres-standby failover.sh`.
- `DATABASE_READ_URL` is optional and routes read-only queries to the standby.

---

## 4. Automatic Backups

```bash
docker compose -f docker-compose.control-plane.yml -f docker-compose.backup.yml up -d backup
```

- Default schedule: **23:00 every day, Asia/Jakarta** (`BACKUP_CRON=0 23 * * *`,
  `TIMEZONE`). Run by supercronic (honours `$TZ`).
- `pg_dump` (custom format) is uploaded to the MinIO bucket `cloudx-backups`.
- Automatic tiering: monthly (day 1), weekly (Sunday), daily.
- Retention: `BACKUP_RETENTION_DAILY=7`, `WEEKLY=4`, `MONTHLY=12`.
- Run an initial backup immediately: `BACKUP_RUN_ON_START=true`.

Restore:
```bash
docker compose exec backup restore.sh                       # latest daily backup
docker compose exec backup restore.sh postgres/daily/xxx.dump
```

### Volume backup (Block Storage → MinIO)
```
POST   /volumes/{id}/backup      → full volume backup to object storage
GET    /backups                  → list backups
GET    /backups/{id}             → details
POST   /backups/{id}/restore     → restore into a new volume
DELETE /backups/{id}             → delete the backup + object
```
The worker exports the volume (LVM via `qemu-img`, Ceph via `rbd export`) and then
uploads it with `mc` to the `cloudx-backups/volumes/` bucket.

---

## 5. Monitoring (Prometheus + Grafana)

```bash
docker compose -f docker-compose.control-plane.yml -f docker-compose.monitoring.yml up -d
```

- **Prometheus**: `http://<node>:9090`, **15-day** retention.
- **Grafana**: `http://<node>:3000` (admin / `GRAFANA_ADMIN_PASSWORD`).
  The datasource + dashboards (Overview, Infrastructure) are pre-provisioned.
- Exporters: node-exporter (9100), postgres-exporter (9187),
  RabbitMQ prometheus plugin (15692), HAProxy (8404).
- Every API exposes `/metrics` (via `shared/metrics.py`):
  `cloudx_api_requests_total`, `cloudx_api_request_latency_seconds`.

Alert rules (`services/prometheus/alerts.yml`): API/node/DB down, P95 latency
> 500ms, 5xx error rate > 5%, replication lag > 1GB.

---

## 6. Internal TLS

TLS covers only **internal service-to-service** communication (external is out of scope).

```bash
# generate a CA + a certificate per service (SAN includes the multi-node management IPs)
SERVICE_HOSTS="10.0.0.11 10.0.0.12" ./services/certs/generate-certs.sh
# output in services/certs/out/ (git-ignored)
```
Distribute `ca.crt` to all nodes, mount `TLS_CERT_DIR` into the containers,
set `TLS_ENABLED=true` and change the `*_API_URL` values to `https://`.

---

## 7. Database Migrations (Alembic)

Alembic is the only path that creates or changes tables. Each service has its own migration history
(`services/<service>/migrations/versions/`) and its own version table (`alembic_version_<service>`), so
services may share one database or use separate ones. Services refuse to start if the schema has not been
migrated. Details in `services/migrations/README.md`.

```bash
sudo make migrate                     # control plane: create/upgrade the schema of all services (idempotent)
cloudx-db-migrate compute auth        # only specific services
cloudx-db-migrate --current           # current revision per service

# create a new migration after changing a model (additive-first, zero-downtime)
cloudx-db-migrate --revision compute -m "description" --autogenerate
```

Zero-downtime rule: add a nullable/default column first → deploy the code →
backfill → only then add constraints / drop the old column (separate migration).

---

## 8. Summary of New Phase 5 Files

```
docker-compose.control-plane.yml   docker-compose.compute-node.yml
docker-compose.storage-node.yml    docker-compose.ha.yml
docker-compose.monitoring.yml      docker-compose.backup.yml
services/postgres/                 (replication entrypoint, failover)
services/rabbitmq/                 (cluster entrypoint, HA policy)
services/backup/                   (cron 23:00 + pg_dump→MinIO + retention)
services/prometheus/               (scrape config + alerts)
services/grafana/provisioning/     (datasource + dashboards)
services/certs/generate-certs.sh   (internal PKI)
services/migrations/               (Alembic scaffold + baseline)
services/shared/metrics.py         (/metrics per service)
```
