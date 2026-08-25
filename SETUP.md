# CloudX Setup Guide

## 🚀 Bootstrap (Fresh Node)

Jalankan langkah berikut secara berurutan saat pertama kali setup node baru.

### Langkah 0 — Konfigurasi `.env`

```bash
cp .env.example .env
nano .env   # sesuaikan variabel di bawah ini
```

Variabel penting:

| Variabel | Contoh | Keterangan |
|---|---|---|
| `NETWORK_INTERFACE` | `enp0s3` | Interface internal (management/API) |
| `PROVIDER_INTERFACE` | `enp0s8` | Interface eksternal (host-only/provider adapter) |
| `INTERNAL_VIP_ADDRESS` | `192.168.10.102/24` | IP host di eksternal (API endpoint/metadata) |
| `IMAGE_STORAGE_BACKEND` | `file` | `file` atau `minio` |
| `METADATA_HMAC_KEY` | *(kosong)* | HMAC key untuk signed instance identity (opsional) |
| `JWT_SECRET` | `changeme` | Secret untuk JWT token — **wajib diganti di produksi** |
| `STORAGE_LVM_DRIVER` | `true` | Aktifkan driver LVM block device |
| `STORAGE_CEPH_RBD_DRIVER` | `false` | Aktifkan driver Ceph RBD |
| `LVM_PV_NAME` | `/dev/sdb` | Physical Volume untuk auto-create VG (wajib jika LVM aktif) |
| `LVM_VG_NAME` | `vg-cloudx` | Nama Volume Group yang akan dibuat otomatis |
| `CEPH_POOL` | `volumes` | Nama pool Ceph (wajib jika Ceph aktif) |

#### Variabel Fase 5 — Production Readiness (opsional)

| Variabel | Default | Keterangan |
|---|---|---|
| `TIMEZONE` | `Asia/Jakarta` | Timezone semua container (log, cron, backup) |
| `CONTROLLER_IP` | — | IP management Control Plane; di-set di compute/storage node multi-node |
| `AUTH_BACKENDS` … `DASHBOARD_BACKENDS` | `127.0.0.1` | Daftar backend HAProxy per service, dipisah koma (multi-node HA) |
| `RABBITMQ_ERLANG_COOKIE` | *(ubah!)* | Shared secret cluster RabbitMQ 3-node (`docker-compose.ha.yml`) |
| `RABBITMQ_SEED_NODE` | `rabbit@rabbitmq-1` | Node seed yang dijoin node lain |
| `POSTGRES_ROLE` | `primary` | `primary` atau `standby` (streaming replication) |
| `POSTGRES_REPLICATION_USER` / `_PASSWORD` | `replicator` | Kredensial replikasi PostgreSQL |
| `POSTGRES_PRIMARY_HOST` | `127.0.0.1` | Alamat primary (di-set di standby) |
| `BACKUP_CRON` | `0 23 * * *` | Jadwal backup DB harian (dievaluasi dalam `$TIMEZONE`) |
| `BACKUP_BUCKET` | `cloudx-backups` | Bucket MinIO/S3 tujuan backup |
| `BACKUP_RETENTION_DAILY/WEEKLY/MONTHLY` | `7` / `4` / `12` | Retensi backup per tier |
| `PROMETHEUS_RETENTION` | `15d` | Retensi data metrics Prometheus |
| `PROMETHEUS_PORT` / `GRAFANA_PORT` | `9090` / `3000` | Port UI monitoring |
| `TLS_ENABLED` | `false` | Aktifkan internal TLS antar-service |
| `TLS_CERT_DIR` | `/etc/cloudx/certs` | Path mount sertifikat internal |

### Langkah 1 — Siapkan Node

```bash
sudo make node-bootstrap
newgrp libvirt   # aktifkan group tanpa re-login
```

Script `scripts/node-bootstrap.sh` melakukan:
1. Install packages: `qemu-kvm`, `libvirt`, `openvswitch-switch`, `ovn-host`, dll
2. Enable `openvswitch-switch` dan `libvirtd`
3. Tambah user ke grup `libvirt` dan `kvm`
4. Buat direktori storage di `/var/lib/mini-openstack/`
5. Buat OVS bridge `br-int` (fail_mode=secure) dan `br-ex`, attach `PROVIDER_INTERFACE`
6. Buat patch port `br-int ↔ br-ex` (wajib untuk provider network)
7. Set IP `INTERNAL_VIP_ADDRESS` ke `br-ex`, tambah alias `169.254.169.254/32`
8. Set iptables DNAT: `169.254.169.254:80 → metadata-proxy → metadata-api`
9. Konfigurasi host OVS untuk OVN (`ovn-remote`, `bridge-mappings`)
10. Install `systemd` service `mini-openstack-network` — semua konfigurasi jaringan dipulihkan otomatis setiap boot

> **Idempotent** — aman dijalankan ulang jika ada yang gagal.

### Langkah 2 — Jalankan Services

#### Opsi A — Single-node all-in-one (default lab)

```bash
# Backend file/lokal
make up

# Backend MinIO (image + volume backup + DB backup)
make up-minio
```

#### Opsi B — Single-node + overlay Fase 5

```bash
make up-minio                 # stack dasar dulu
make up-monitoring            # + Prometheus :9090, Grafana :3000, exporters
make up-backup                # + backup runner otomatis 23:00 Asia/Jakarta
```

#### Opsi C — Single-node + High Availability

```bash
docker compose -f docker-compose.yml -f docker-compose.ha.yml up -d --build
```

#### Opsi D — Multi-node (role terpisah)

```bash
# ── Di Control Plane ──────────────────────────────────────────────────────────
cp .env.example .env          # set CONTROLLER_IP=<ip node ini>, secrets, dst.
docker compose -f docker-compose.control-plane.yml up -d --build

# ── Di Storage Node ───────────────────────────────────────────────────────────
cp .env.example .env          # set CONTROLLER_IP=<ip control plane>, LVM_PV_NAME / CEPH_POOL
docker compose -f docker-compose.storage-node.yml up -d --build

# ── Di Compute Node ───────────────────────────────────────────────────────────
cp .env.example .env          # set CONTROLLER_IP=<ip control plane>, NETWORK_INTERFACE
docker compose -f docker-compose.compute-node.yml up -d --build
```

> Detail lengkap topologi multi-node, HA, backup, monitoring, dan internal TLS:
> [docs/fase5-production-readiness.md](docs/fase5-production-readiness.md).

Install CLI:
```bash
make install-cli
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

### Langkah 3 — Setup Credentials

```bash
# Konfigurasi access key credentials (dari Dashboard)
cloudx auth configure
```

File credentials tersimpan di `~/.cloudx/credentials` dan `~/.cloudx/config`.

### Langkah 4 — Setup Lab Resources

Jalankan perintah-perintah berikut untuk melakukan inisialisasi awal resource cloud Anda:

```bash
# 1. Buat provider network

cloudx network create public-net --external
cloudx subnet create public-subnet \
    --network public-net \
    --cidr 192.168.10.0/24 \
    --gateway 192.168.10.254 \
    --no-dhcp \
    --allocation-start 192.168.10.110 \
    --allocation-end 192.168.10.120

# 2. Buat private network
cloudx network create private-net
cloudx subnet create private-subnet \
    --network private-net \
    --cidr 192.168.12.0/24 \
    --gateway 192.168.12.1

# 3. Buat router
cloudx router create router1
cloudx router add-interface router1 --subnet private-subnet
cloudx router set-gateway router1 public-net

# 4. Host route
sudo ip route add 192.168.12.0/24 via 192.168.10.254 dev br-ex

# 5. Upload image
cloudx image upload cirros \
    --file /tmp/cirros-0.6.2.img \
    --disk-format qcow2
```

### ✅ Node Siap

**Penting:** Sebelum membuat VM pertama, admin harus terlebih dahulu membuat volume types.

### Langkah 5 — Buat Volume Types (Admin, Wajib)

Volume types adalah **performance profile** mirip AWS EBS. Tidak ada default — admin harus membuatnya setelah services naik.
LVM dan Ceph adalah **backend penyimpanan**, bukan nama volume type.

```bash
# List types yang ada (kosong setelah install baru)
cloudx volume-type list

# Buat types sesuai kebutuhan infrastruktur
# Format: cloudx volume-type create <NAMA> --backend <lvm|ceph> --disk-type <tier> [options]

# ── Dengan backend LVM ──────────────────────────────────────────────────────────

cloudx volume-type create gp2 \
    --backend lvm \
    --disk-type sata_ssd \
    --max-iops 3000 \
    --max-throughput 125 \
    --description "General Purpose SSD"

cloudx volume-type create gp3 \
    --backend lvm \
    --disk-type sas_ssd \
    --max-iops 16000 \
    --max-throughput 1000 \
    --description "Throughput Optimized SSD"

cloudx volume-type create io1 \
    --backend lvm \
    --disk-type nvme_ssd \
    --max-iops 64000 \
    --max-throughput 4000 \
    --description "Provisioned IOPS NVMe SSD"

cloudx volume-type create sc1 \
    --backend lvm \
    --disk-type sata_hdd \
    --max-iops 250 \
    --max-throughput 250 \
    --description "Cold HDD"

# ── Dengan backend Ceph RBD (STORAGE_CEPH_RBD_DRIVER=true) ─────────────────────

cloudx volume-type create io2 \
    --backend ceph \
    --disk-type nvme_ssd \
    --max-iops 64000 \
    --max-throughput 4000 \
    --description "Provisioned IOPS NVMe SSD (Ceph RBD)"

# Verifikasi
cloudx volume-type list
# NAME  DISK_TYPE  BACKEND  MAX_IOPS  MAX_THROUGHPUT
# gp2   sata_ssd   lvm      3000      125
# gp3   sas_ssd    lvm      16000     1000
# io1   nvme_ssd   lvm      64000     4000
# sc1   sata_hdd   lvm      250       250
```

> **Disk type tersedia:** `sata_ssd` · `sas_ssd` · `nvme_ssd` · `sata_hdd` · `sas_hdd`
> Nilai IOPS dan throughput bersifat **informational only** — tidak di-enforce oleh hypervisor.
> Jika tidak ada volume type, pembuatan volume akan gagal dengan error yang jelas.

### ✅ Node Siap (Uji Coba)

```bash
# Buat keypair
cloudx keypair create mykey > mykey.pem && chmod 600 mykey.pem

# Buat VM
cloudx server create vm1 \
    --image cirros \
    --flavor tiny \
    --network private-net \
    --key-name mykey

# Lihat status
cloudx server list
cloudx server show vm1
```

---

## 🌍 Multi-Node Setup (High Availability)

Arsitektur ini mendukung deployment **Multi-Node** menggunakan Keepalived (VRRP) untuk High Availability pada sisi API dan Control Plane.

Untuk menambahkan node sekunder (BACKUP), ikuti langkah-langkah berikut di node baru:

### 1. Salin Konfigurasi `.env`
Salin file `.env` dari node MASTER ke node BACKUP. Pastikan `JWT_SECRET`, `AUTH_SECRET_KEY`, dan kredensial layanan (seperti `POSTGRES_PASSWORD`, `RABBITMQ_PASS`) **sama persis**.

### 2. Sesuaikan Variabel Jaringan & HA di Node Backup
Edit file `.env` di node BACKUP, dan sesuaikan nilai berikut:

```bash
# Sesuaikan interface fisik node backup (jika berbeda)
NETWORK_INTERFACE=enp0s3
PROVIDER_INTERFACE=enp0s8

# VIP harus SAMA persis dengan node Master (contoh: 192.168.10.102/24)
INTERNAL_VIP_ADDRESS=192.168.10.102/24

# ── Konfigurasi Keepalived (Ubah untuk node Backup) ──
KEEPALIVED_STATE=BACKUP         # <-- Ubah ke BACKUP
KEEPALIVED_PRIORITY=50          # <-- Lebih rendah dari Master (100)
KEEPALIVED_ROUTER_ID=51         # <-- HARUS SAMA dengan Master
KEEPALIVED_AUTH_PASS=cloudx01   # <-- HARUS SAMA dengan Master
```

### 3. Arahkan Database & RabbitMQ (Opsional / Tergantung Topologi)
> **Catatan:** Pada arsitektur saat ini, layanan stateful (PostgreSQL, RabbitMQ, OVN DB) belum di-cluster. Jika Anda ingin semua node terhubung ke satu sumber kebenaran, arahkan URL layanan di `.env` node BACKUP ke IP node MASTER:

```bash
DATABASE_URL=postgresql+psycopg://mini:mini@<IP_MASTER>:5432/mini_openstack
RABBITMQ_URL=amqp://cloudx:cloudx@<IP_MASTER>:5672/
OVN_NB_DB=tcp:<IP_MASTER>:6641
OVN_SB_DB=tcp:<IP_MASTER>:6642
```
> Anda juga dapat men-disable service `postgres`, `rabbitmq`, dan `ovn-db` di `docker-compose.yml` node BACKUP jika hanya ingin menjalankan API dan Compute Worker.

### 4. Jalankan Node Backup
Jalankan langkah bootstrap yang sama seperti di atas:
```bash
sudo make node-bootstrap
newgrp libvirt
make up
```

Jika node MASTER mati, Keepalived di node BACKUP akan mendeteksi hilangnya heartbeat dan secara otomatis mengambil alih IP `INTERNAL_VIP_ADDRESS`. HAProxy di node BACKUP akan mulai merutekan permintaan API ke layanan lokalnya.

---

## 🔄 Setelah Reboot Node

```bash
make start   # = make up
```

Yang otomatis terjadi:
- `openvswitch-switch` start → `br-int` dan `br-ex` aktif kembali
- `mini-openstack-network.service` → memulihkan IP, alias metadata, route, iptables DNAT
- `docker compose up` → semua containers start

> Jika floating IP tidak bekerja setelah restart:
> ```bash
> sudo ./scripts/fix-localnet-flow.sh
> ```
