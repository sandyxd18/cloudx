# 🚀 Fase 5 — Production Readiness & Multi-Node Deployment

Panduan operasional untuk fitur Fase 5 CloudX: deployment multi-node adaptif,
High Availability, backup otomatis, monitoring, dan internal TLS.

Semua fitur bersifat **adaptif**: bisa dijalankan single-node (all-in-one) untuk
dev/lab, atau dipecah ke beberapa node untuk pseudo-production.

---

## 1. Topologi Node

| Role | Compose file | Komponen |
|------|-------------|----------|
| **All-in-One** (single node) | `docker-compose.yml` | Semua service dalam satu host |
| **Control Plane** | `docker-compose.control-plane.yml` | PostgreSQL, RabbitMQ, OVN NB/SB+northd, semua API, dashboard, HAProxy, Keepalived |
| **Compute Node** | `docker-compose.compute-node.yml` | compute-worker (libvirt) + ovn-controller lokal |
| **Storage Node** | `docker-compose.storage-node.yml` | block-store-worker (LVM/Ceph) + MinIO |

Variabel kunci di `.env`:

```bash
TIMEZONE=Asia/Jakarta            # timezone semua container (log, cron, backup)
CONTROLLER_IP=10.0.0.11          # IP management control plane (dipakai compute/storage node)
NETWORK_INTERFACE=enp0s3         # NIC management → Geneve tunnel OVN
PROVIDER_INTERFACE=enp0s8        # NIC provider → br-ex (gateway node)
```

### Prinsip Storage
Compute node **tidak** punya disk VM lokal. Root disk & volume VM dilayani dari
**Storage Node** (Ceph RBD) melalui management network. Compute node hanya
menyimpan libvirt XML + config-drive ISO secara ephemeral.

### Prinsip Network (OVN, seperti OpenStack)
- `ovn-nb-db`, `ovn-sb-db`, `ovn-northd` **terpusat** di Control Plane.
- `ovn-controller` berjalan di **setiap** compute/gateway node, terhubung ke
  SB DB pusat via `tcp:${CONTROLLER_IP}:6642`.

---

## 2. Deployment Multi-Node

### 2.1 Control Plane (node controller)
```bash
cp .env.example .env
# set CONTROLLER_IP, NETWORK_INTERFACE, secrets
docker compose -f docker-compose.control-plane.yml up -d
```

### 2.2 Storage Node
```bash
# .env: CONTROLLER_IP=<ip controller>, LVM_PV_NAME / CEPH_POOL
docker compose -f docker-compose.storage-node.yml up -d
```

### 2.3 Compute Node
```bash
# .env: CONTROLLER_IP=<ip controller>, NETWORK_INTERFACE
docker compose -f docker-compose.compute-node.yml up -d
```

### 2.4 Single-Node (adaptif)
```bash
docker compose up -d          # semua role di satu host — tidak ada yang berubah
```

---

## 3. High Availability

### 3.1 HAProxy Multi-Backend
HAProxy me-load-balance API ke banyak instance. Set daftar backend di `.env`:

```bash
AUTH_BACKENDS=10.0.0.11,10.0.0.12,10.0.0.13
COMPUTE_BACKENDS=10.0.0.11,10.0.0.12,10.0.0.13
# dst untuk NETWORKING/IMAGE/BLOCK_STORE/PLACEMENT/DASHBOARD
```
Single controller cukup biarkan `127.0.0.1` (default). Round-robin + health
check `/health` aktif otomatis. Stats: `http://<node>:8404/stats`,
metrics Prometheus: `http://<node>:8404/metrics`.

### 3.2 RabbitMQ Cluster (3 node)
```bash
docker compose -f docker-compose.control-plane.yml -f docker-compose.ha.yml up -d
```
- Erlang cookie sama di semua node (`RABBITMQ_ERLANG_COOKIE`).
- Node non-seed otomatis `join_cluster` ke `RABBITMQ_SEED_NODE`.
- Policy `ha-all` memirror semua classic queue ke seluruh node.

### 3.3 PostgreSQL Streaming Replication
- `POSTGRES_ROLE=primary` → membuat replication role + `pg_hba` untuk standby.
- `POSTGRES_ROLE=standby` → `pg_basebackup` dari `POSTGRES_PRIMARY_HOST` lalu
  jalan sebagai hot standby read-only.
- Failover manual (lab): `docker compose exec postgres-standby failover.sh`.
- `DATABASE_READ_URL` opsional untuk route query read-only ke standby.

---

## 4. Backup Otomatis

```bash
docker compose -f docker-compose.control-plane.yml -f docker-compose.backup.yml up -d backup
```

- Jadwal default: **23:00 setiap hari, Asia/Jakarta** (`BACKUP_CRON=0 23 * * *`,
  `TIMEZONE`). Dijalankan oleh supercronic (menghormati `$TZ`).
- `pg_dump` (format custom) diupload ke MinIO bucket `cloudx-backups`.
- Tiering otomatis: monthly (tgl 1), weekly (Minggu), daily.
- Retention: `BACKUP_RETENTION_DAILY=7`, `WEEKLY=4`, `MONTHLY=12`.
- Jalankan backup awal langsung: `BACKUP_RUN_ON_START=true`.

Restore:
```bash
docker compose exec backup restore.sh                       # backup daily terbaru
docker compose exec backup restore.sh postgres/daily/xxx.dump
```

### Volume Backup (Cinder → MinIO)
```
POST   /volumes/{id}/backup      → backup penuh volume ke object storage
GET    /backups                  → list backup
GET    /backups/{id}             → detail
POST   /backups/{id}/restore     → restore ke volume baru
DELETE /backups/{id}             → hapus backup + object
```
Worker mengekspor volume (LVM via `qemu-img`, Ceph via `rbd export`) lalu
upload `mc` ke bucket `cloudx-backups/volumes/`.

---

## 5. Monitoring (Prometheus + Grafana)

```bash
docker compose -f docker-compose.control-plane.yml -f docker-compose.monitoring.yml up -d
```

- **Prometheus**: `http://<node>:9090`, retention **15 hari**.
- **Grafana**: `http://<node>:3000` (admin / `GRAFANA_ADMIN_PASSWORD`).
  Datasource + dashboard (Overview, Infrastructure) sudah ter-provision.
- Exporters: node-exporter (9100), postgres-exporter (9187),
  RabbitMQ prometheus plugin (15692), HAProxy (8404).
- Setiap API meng-expose `/metrics` (via `shared/metrics.py`):
  `cloudx_api_requests_total`, `cloudx_api_request_latency_seconds`.

Alert rules (`services/prometheus/alerts.yml`): API/node/DB down, P95 latency
> 500ms, error-rate 5xx > 5%, replication lag > 1GB.

---

## 6. Internal TLS

TLS hanya untuk komunikasi **antar service internal** (external out of scope).

```bash
# generate CA + cert per service (SAN termasuk management IP multi-node)
SERVICE_HOSTS="10.0.0.11 10.0.0.12" ./services/certs/generate-certs.sh
# hasil di services/certs/out/ (di-gitignore)
```
Distribusikan `ca.crt` ke semua node, mount `TLS_CERT_DIR` ke container,
set `TLS_ENABLED=true` dan ubah `*_API_URL` menjadi `https://`.

---

## 7. Database Migrations (Alembic)

CloudX kini punya scaffold Alembic bersama di `services/migrations/`.

```bash
# adopsi schema existing sebagai baseline (sekali saja)
alembic -c services/migrations/alembic.ini stamp base_0001

# buat migrasi baru (additive-first, zero-downtime)
alembic -c services/migrations/alembic.ini revision -m "deskripsi"
alembic -c services/migrations/alembic.ini upgrade head
```

Aturan zero-downtime: tambah kolom nullable/default dulu → deploy code →
backfill → baru tambah constraint / drop kolom lama (migrasi terpisah).

---

## 8. Ringkasan File Baru Fase 5

```
docker-compose.control-plane.yml   docker-compose.compute-node.yml
docker-compose.storage-node.yml    docker-compose.ha.yml
docker-compose.monitoring.yml      docker-compose.backup.yml
services/postgres/                 (entrypoint replikasi, failover)
services/rabbitmq/                 (cluster entrypoint, HA policy)
services/backup/                   (cron 23:00 + pg_dump→MinIO + retention)
services/prometheus/               (scrape config + alerts)
services/grafana/provisioning/     (datasource + dashboards)
services/certs/generate-certs.sh   (internal PKI)
services/migrations/               (Alembic scaffold + baseline)
services/shared/metrics.py         (/metrics per service)
```
