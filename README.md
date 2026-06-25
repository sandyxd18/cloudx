# CloudX — Private Cloud Lab

**CloudX** adalah private cloud platform single-node berbasis Docker Compose yang mengimplementasikan API mirip OpenStack. Compute menggunakan libvirt + QEMU/KVM, networking menggunakan OVN/OVS, inter-service communication via **RabbitMQ** (AMQP), dan seluruh stack dikelola melalui CLI `cloudx` serta web Dashboard.

> **Untuk lab lokal & pembelajaran.** Bukan untuk production. Single-node tanpa live migration atau multi-scheduler.

---

## ✨ Fitur

### Compute (Nova-like)
- Real VM via libvirt/QEMU/KVM (volume-first architecture)
- Flavor management (vCPU, RAM, Disk)
- Keypair management (SSH public key injection via cloud-init)
- Server actions: start, stop, reboot (soft/hard), **pause/resume**, **suspend/unsuspend**, **resize** (live vCPU+RAM), **rebuild**, **rescue/unrescue**
- **Async operations via RabbitMQ** — server create/delete/actions return immediately with transitional status
- **Conductor service** (nova-conductor equivalent) — workers never access DB directly
- **Live Dashboard updates via WebSocket** — status changes pushed in real-time
- Password injection via QEMU Guest Agent
- Console log retrieval via PTY
- Server Groups (affinity / anti-affinity)
- Multi-NIC (attach/detach port ke VM yang berjalan)

### Networking (Neutron-like)
- Network & Subnet management dengan DHCP/IPAM via OVN
- Router dengan external gateway dan interface subnet
- Floating IP (OVN DNAT/SNAT)
- Security Groups berbasis OVN Port Group & ACL (ingress/egress rules)
- Provider network via `br-ex` / OVN localnet

### Image (Glance-like)
- Upload/download image OS (qcow2, raw, iso)
- Backend: **file lokal** atau **MinIO** (S3-compatible)
- Visibility: public / private (per-project scoping)
- Deactivate / reactivate image

### Block Storage (Cinder-like)
- Volume lifecycle: create, delete, attach, detach, extend
- Snapshot & backup volume
- **Volume Types**: AWS EBS-style performance profiles (gp2, gp3, io1, io2, sc1, st1, dll.) — admin-defined, tidak ada default
- **Backend**: LVM block device (`/dev/VG/LV`) dan/atau Ceph RBD (network disk) — dipilih saat membuat volume type
- **Async operations via RabbitMQ** — create/delete/extend/snapshot langsung return, worker kerjakan di background
- Boot-from-volume architecture (semua VM boot dari persistent root volume)
- Volume transfer antar-project

### Metadata (Nova metadata-like)
- EC2-compatible: `/latest/meta-data/*`, `/latest/user-data`
- OpenStack-compatible: `/openstack/latest/meta_data.json`, `network_data.json`, `vendor_data.json`
- `vendor_data2.json` (format v2 dengan `static` + `dynamic.instance-identity`)
- Instance identity document dengan HMAC-SHA256 signature (opsional)
- Multi-NIC support di `network_data.json`
- Metadata proxy via `169.254.169.254` untuk cloud-init

### Placement (Nova Placement-like)
- Resource Provider & Inventory (VCPU, MEMORY_MB, DISK_GB)
- Allocation tracking per consumer (server)
- Usage & quota reporting per project
- Traits support

### Auth (Keystone-like)
- JWT-based authentication
- Multi-project support (project scoping)
- User management dengan role (admin/member/reader)
- Access Key (AWS-style credentials untuk CLI)
- Group management

### CLI (`cloudx`)
- Unified CLI mirip OpenStack CLI / AWS CLI
- Autentikasi via credentials file (`~/.cloudx/credentials` + `~/.cloudx/config`)
- Subcommands: `auth`, `server`, `flavor`, `keypair`, `network`, `subnet`, `router`, `floating-ip`, `security-group`, `image`, `volume`, `snapshot`, `placement`

### Dashboard (Web UI)
- React + Vite frontend dengan dark theme premium
- Real-time stats: CPU/RAM usage charts, resource utilization bars
- Full CRUD untuk semua resource via web UI
- **WebSocket live events** — server status updates tanpa manual refresh
- Served via FastAPI proxy di port 8080

---

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
| `PROVIDER_INTERFACE` | `enp0s8` | Interface host-only adapter |
| `PROVIDER_HOST_IP` | `192.168.10.102/24` | IP host di provider network (dipasang ke `br-ex`) |
| `PROVIDER_CIDR` | `192.168.10.0/24` | Subnet provider/external network |
| `PROVIDER_GATEWAY` | `192.168.10.254` | OVN router external IP (gateway di `public-net`) |
| `PROVIDER_ALLOCATION_START` | `192.168.10.110` | Rentang floating IP mulai |
| `PROVIDER_ALLOCATION_END` | `192.168.10.120` | Rentang floating IP sampai |
| `PRIVATE_CIDR` | `192.168.12.0/24` | Subnet private/tenant network |
| `PRIVATE_GATEWAY` | `192.168.12.1` | Gateway private network |
| `IMAGE_STORAGE_BACKEND` | `file` | `file` atau `minio` |
| `METADATA_HMAC_KEY` | *(kosong)* | HMAC key untuk signed instance identity (opsional) |
| `JWT_SECRET` | `changeme` | Secret untuk JWT token — **wajib diganti di produksi** |
| `STORAGE_LVM_DRIVER` | `true` | Aktifkan driver LVM block device |
| `STORAGE_CEPH_RBD_DRIVER` | `false` | Aktifkan driver Ceph RBD |
| `LVM_PV_NAME` | `/dev/sdb` | Physical Volume untuk auto-create VG (wajib jika LVM aktif) |
| `LVM_VG_NAME` | `vg-cloudx` | Nama Volume Group yang akan dibuat otomatis |
| `CEPH_POOL` | `volumes` | Nama pool Ceph (wajib jika Ceph aktif) |

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
7. Set IP `PROVIDER_HOST_IP` ke `br-ex`, tambah alias `169.254.169.254/32`
8. Set iptables DNAT: `169.254.169.254:80 → metadata-proxy → metadata-api`
9. Konfigurasi host OVS untuk OVN (`ovn-remote`, `bridge-mappings`)
10. Install `systemd` service `mini-openstack-network` — semua konfigurasi jaringan dipulihkan otomatis setiap boot

> **Idempotent** — aman dijalankan ulang jika ada yang gagal.

### Langkah 2 — Jalankan Services

```bash
# Opsi A — Backend file/lokal (default)
make up

# Opsi B — Backend MinIO
make up-minio
```

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

**Otomatis (direkomendasikan):**
```bash
make lab-setup
```

Script ini membuat secara idempotent:
1. `public-net` (provider/external network) + `public-subnet`
2. `private-net` + `private-subnet` (tenant network)
3. `router1` dengan gateway ke `public-net` dan interface ke `private-subnet`
4. Host route: `PRIVATE_CIDR via PROVIDER_GATEWAY dev br-ex` (wajib untuk metadata + floating IP)
5. Sync security group ke semua port VM yang ada
6. Download CirrOS image dan upload ke image service

**Manual:**
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

### ✅ Node Siap

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

## 🔄 Setelah Reboot Node

```bash
make start   # = make up + make lab-setup (keduanya idempotent)
```

Yang otomatis terjadi:
- `openvswitch-switch` start → `br-int` dan `br-ex` aktif kembali
- `mini-openstack-network.service` → memulihkan IP, alias metadata, route, iptables DNAT
- `docker compose up` → semua containers start

> Jika floating IP tidak bekerja setelah restart:
> ```bash
> sudo ./scripts/fix-localnet-flow.sh
> ```

---

## 🌐 Service Ports

| Service | URL | Keterangan |
|---|---|---|
| **Dashboard** | http://localhost:8080 | Web UI (React + Vite) + WebSocket |
| `auth-api` | http://localhost:8005 | Identity & token service |
| `compute-api` | http://localhost:8003 | Nova-like compute API (RPC publisher) |
| `compute-worker` | *(AMQP consumer)* | Libvirt provisioning worker (no HTTP port) |
| `conductor` | *(AMQP consumer)* | DB proxy for workers (nova-conductor equivalent) |
| `networking-api` | http://localhost:8001 | Neutron-like networking API |
| `image-api` | http://localhost:8002 | Glance-like image API |
| `block-store-api` | http://localhost:8004 | Cinder-like volume API (RPC publisher, no privileged) |
| `block-store-worker` | *(AMQP consumer)* | LVM/Ceph storage worker (privileged, no HTTP port) |
| `placement-api` | http://localhost:8006 | Resource placement & tracking |
| `metadata-api` | http://localhost:8775 | Nova metadata HTTP API |
| `metadata-proxy` | http://localhost:8776 | Metadata proxy (169.254.169.254) |
| **RabbitMQ** | amqp://localhost:5672 | AMQP message broker |
| RabbitMQ Management | http://localhost:15672 | RabbitMQ web UI (cloudx/cloudx) |
| MinIO API | http://localhost:9000 | S3-compatible object storage |
| MinIO Console | http://localhost:9001 | MinIO web UI |

Swagger API tersedia di `/docs` pada masing-masing port service.

---

## 💻 CLI Reference

### Auth

```bash
cloudx auth configure            # Setup access key credentials
cloudx auth show                 # Tampilkan info token aktif
cloudx project list              # List projects
cloudx project create myproject  # Buat project
cloudx user list                 # List users
cloudx user create alice --role member
```

### Compute

```bash
# Flavor
cloudx flavor list
cloudx flavor create m1.custom --vcpus 2 --ram 2048 --disk 20

# Keypair
cloudx keypair create mykey > mykey.pem && chmod 600 mykey.pem
cloudx keypair list

# Server
cloudx server list
cloudx server create vm1 --image cirros --flavor tiny \
    --network private-net --key-name mykey --security-group default
cloudx server show vm1
cloudx server start vm1
cloudx server stop vm1
cloudx server reboot vm1                  # soft reboot
cloudx server reboot vm1 --hard           # hard reboot
cloudx server pause vm1                   # pause (hypervisor-level)
cloudx server resume vm1                  # resume dari pause
cloudx server suspend vm1                 # suspend ke disk (managed save)
cloudx server unsuspend vm1              # restore dari managed save
cloudx server resize vm1 --vcpus 4 --ram 4096   # live resize
cloudx server rebuild vm1 --image ubuntu  # ganti root disk
cloudx server rescue vm1 --image rescue-img      # boot dari rescue image
cloudx server unrescue vm1               # kembali ke disk asli
cloudx server console vm1                # serial console
cloudx server logs vm1                   # console log output
cloudx server delete vm1
cloudx server delete vm1 --keep-root-volume

# Server Groups
cloudx server-group create my-sg --policy anti-affinity
cloudx server create vm2 --server-group my-sg ...
```

### Networking

```bash
cloudx network list
cloudx network create private-net
cloudx subnet create private-subnet --network private-net --cidr 192.168.12.0/24
cloudx router create router1
cloudx router add-interface router1 --subnet private-subnet
cloudx router set-gateway router1 public-net

cloudx security-group create web --description "Allow HTTP and SSH"
cloudx security-group rule create web --direction ingress --protocol tcp --port 22
cloudx security-group rule create web --direction ingress --protocol tcp --port 80
cloudx security-group rule create web --direction ingress --protocol icmp

cloudx floating-ip list
cloudx floating-ip create public-net
cloudx floating-ip associate 192.168.10.110 --server vm1 --router router1
cloudx floating-ip disassociate 192.168.10.110
cloudx floating-ip delete 192.168.10.110

# Multi-NIC
cloudx port create vm1-net2 --network net2
cloudx server add-port vm1 vm1-net2
cloudx server remove-port vm1 vm1-net2
```

### Image

```bash
cloudx image list
cloudx image upload myimage --file ./image.qcow2 --disk-format qcow2
cloudx image show cirros
cloudx image set cirros --visibility public
cloudx image deactivate myimage
cloudx image reactivate myimage
cloudx image delete myimage
```

### Block Storage

> **Perhatian:** Volume types harus dibuat oleh admin terlebih dahulu (lihat [Langkah 5 Bootstrap](#langkah-5--buat-volume-types-admin-wajib)).
> Backend LVM dan Ceph adalah infrastruktur penyimpanan — bukan nama volume type.

```bash
# Volume Types (admin) — AWS EBS-style performance profiles
cloudx volume-type list
cloudx volume-type create gp2 --backend lvm  --disk-type sata_ssd --max-iops 3000  --max-throughput 125
cloudx volume-type create gp3 --backend lvm  --disk-type sas_ssd  --max-iops 16000 --max-throughput 1000
cloudx volume-type create io1 --backend lvm  --disk-type nvme_ssd --max-iops 64000 --max-throughput 4000
cloudx volume-type create io2 --backend ceph --disk-type nvme_ssd --max-iops 64000 --max-throughput 4000
cloudx volume-type create sc1 --backend lvm  --disk-type sata_hdd --max-iops 250  --max-throughput 250
cloudx volume-type show gp2
cloudx volume-type set gp2 --description "Updated description" --max-iops 3500
cloudx volume-type delete sc1       # gagal jika ada volume yang menggunakan type ini

# Volume
cloudx volume list
cloudx volume create vol1 --size 10               # auto-select type pertama (alphabetical)
cloudx volume create vol1 --size 10 --type gp2    # eksplisit volume type
cloudx volume create vol2 --size 50 --type gp3    # SAS SSD
cloudx volume create vol3 --size 100 --type io2   # NVMe di Ceph
cloudx volume create ubuntu-root --size 20 --image ubuntu --type gp2
cloudx volume create snap-vol --size 20 --snapshot snap1  # dari snapshot
cloudx volume create clone --size 20 --source vol1        # clone volume
cloudx volume show vol1
cloudx volume attach vol1 --server vm1
cloudx volume detach vol1
cloudx volume extend vol1 --size 30
cloudx volume delete vol1

# Snapshot
cloudx snapshot list
cloudx snapshot show snap1
cloudx volume snapshot vol1 --name snap1
cloudx snapshot delete snap1

# Backup & Transfer
cloudx volume backup vol1 --name backup-$(date +%Y%m%d)
cloudx volume transfer-create vol1 --name "transfer-to-alice"
cloudx volume transfer-accept <transfer_id> --auth-key <key>

# Boot from existing volume
cloudx server create vm2 --boot-volume <root_volume_id> --flavor tiny --network private-net
```

### Placement

```bash
cloudx placement show                    # usage per project
cloudx placement provider list           # resource providers
```

---

## 🏗️ Architecture

```
                          ┌─────────────┐
                          │  Dashboard  │  :8080  (React + Vite)
                          │  (FastAPI   │◄───── Browser
                          │   Proxy)   │
                          └──────┬──────┘
                                 │ /api/*
          ┌──────────────────────┼───────────────────────┐
          │                      │                       │
   ┌──────▼──────┐  ┌────────────▼───┐  ┌──────────────▼──┐
   │  auth-api   │  │  compute-api   │  │ networking-api  │
   │  :8005      │  │  :8003         │  │  :8001          │
   └──────┬──────┘  └────────┬───────┘  └────────┬────────┘
          │                  │                    │
          │           ┌──────▼───────┐     ┌──────▼──────┐
          │           │compute-worker│     │   OVN NB DB │
          │           │  :8010       │     │   :6641     │
          │           │ (libvirt)    │     └──────┬──────┘
          │           └──────────────┘            │
   ┌──────▼──────┐  ┌──────────────┐  ┌──────────▼──────┐
   │  image-api  │  │block-store   │  │ovs-ovn-controller│
   │  :8002      │  │  :8004       │  │ (host network)   │
   └──────┬──────┘  └──────────────┘  └──────────────────┘
          │
   ┌──────▼──────┐  ┌──────────────┐  ┌──────────────┐
   │  MinIO      │  │ placement-api│  │ metadata-api │
   │  :9000      │  │  :8006       │  │  :8775       │
   └─────────────┘  └──────────────┘  └──────────────┘

   PostgreSQL :5432 ── shared database untuk semua services
```

| Service | Peran |
|---|---|
| `auth-api` | Identity: JWT token, project, user, access key |
| `compute-api` | Nova-like: server lifecycle, flavor, keypair, server group |
| `compute-worker` | Provisioning VM via libvirt, qcow2, tap, OVS/OVN |
| `networking-api` | Neutron-like: network, subnet, router, floating IP, security group |
| `image-api` | Glance-like: upload/download image, backend file/MinIO |
| `block-store-api` | Cinder-like: volume, snapshot, backup, extend, attach/detach |
| `placement-api` | Resource tracking: provider, inventory, allocation, usage |
| `metadata-api` | Nova metadata HTTP (169.254.169.254-like), EC2 + OpenStack format |
| `metadata-proxy` | DNAT proxy: redirect 169.254.169.254:80 → metadata-api |
| `dashboard` | React web UI + FastAPI reverse proxy untuk semua API |
| `ovs-ovn-controller` | OVN controller (`network_mode: host`) — kelola OVS bridge di host |
| `ovn-nb-db` / `ovn-sb-db` | OVN Northbound & Southbound database |
| `postgres` | Single database `mini_openstack` untuk semua services |

---

## 🗂️ Makefile Reference

```
make node-bootstrap      # Langkah 1: siapkan host (sudo, sekali saja)
make up                  # Langkah 2A: jalankan services (backend: file)
make up-minio            # Langkah 2B: jalankan services + MinIO
make lab-setup           # Langkah 4: buat lab resources (idempotent)
make start               # Shortcut: make up + make lab-setup
make start-minio         # Shortcut: make up-minio + make lab-setup
make install-cli         # Install cloudx CLI

make down                # Stop semua containers
make logs                # Tail logs semua services
make ps                  # Status containers

make dashboard-dev       # Vite dev server (requires node di host)

make cleanup-compute     # Hapus VM dan tap interface lab
make cleanup-network     # Hapus networking resources lab
make cleanup-storage     # Hapus volume storage lab
make cleanup-all         # cleanup semua resource lab
make test-real-vm        # Integration test: buat VM nyata
make test-floating-ip    # Integration test: floating IP end-to-end
make test-metadata       # Integration test: metadata service
make test-security-group # Integration test: security group
make test-volume-boot    # Integration test: boot from volume
```

---

## 🌐 Floating IP Workflow

```bash
# Alokasi floating IP dari public-net
cloudx floating-ip create public-net

# Associate ke VM
cloudx floating-ip associate 192.168.10.110 --server vm1 --router router1

# Test dari node
ping 192.168.10.110
sudo arping -I br-ex -c 3 192.168.10.110
```

Floating IP adalah NAT mapping di OVN, bukan IP yang dipasang di VM:
```
Client → br-ex → OVN ARP proxy → 192.168.10.110
                 → OVN dnat_and_snat → 192.168.12.x (VM)
```

> **VirtualBox:** Aktifkan **Promiscuous Mode → Allow All** pada adapter host-only di VM settings agar host fisik bisa ping floating IP.

### Troubleshooting Floating IP

```bash
# 1. Cek patch port antara br-int dan br-ex
sudo ovs-vsctl show | grep patch
# Harus ada: patch-br-int-to-br-ex dan patch-br-ex-to-br-int

# 2. Cek gateway chassis terikat
sudo ovn-nbctl --db=tcp:localhost:6641 show | grep -A3 "lrp-router1-external"

# 3. Cek OVN flows terinstall
sudo ovs-ofctl dump-flows br-int | grep "metadata=0x1" | wc -l
# Harus > 0

# 4. Jika flows = 0, jalankan fix script
sudo ./scripts/fix-localnet-flow.sh
```

---

## 🔐 Volume-First Architecture

Semua VM boot dari root volume. Tidak ada ephemeral root disk.

```
Image → Root Volume → VM
```

```bash
# Boot dari image (auto-create root volume)
cloudx server create vm1 --image cirros --flavor tiny --network private-net

# Hapus VM tapi simpan root volume
cloudx server delete vm1 --keep-root-volume

# Boot dari volume existing
cloudx server create vm2 --boot-volume <root_volume_id> --flavor tiny --network private-net
```

---

## 🔍 Debug & Diagnostik

```bash
# OVS & OVN
sudo ovs-vsctl show
sudo ovs-ofctl dump-flows br-int | wc -l
docker compose exec networking-api ovn-nbctl --db=tcp:ovn-nb-db:6641 show
docker compose exec networking-api ovn-nbctl --db=tcp:ovn-nb-db:6641 lr-nat-list router1
docker compose exec ovn-sb-db ovn-sbctl --db=tcp:ovn-sb-db:6642 show

# Libvirt
virsh -c qemu:///system list --all

# Metadata
server_id="$(cloudx server show vm1 | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
curl "http://localhost:8775/latest/meta-data/instance-id?instance_id=${server_id}"
curl "http://localhost:8775/openstack/latest/meta_data.json?instance_id=${server_id}"
curl "http://localhost:8775/openstack/latest/network_data.json?instance_id=${server_id}"
curl "http://localhost:8775/openstack/latest/vendor_data2.json?instance_id=${server_id}"
curl "http://localhost:8775/openstack/latest/identity?instance_id=${server_id}"

# Compute worker health
curl http://localhost:8010/health | python3 -m json.tool

# Placement usage
cloudx placement show
```

---

## 🧹 Cleanup

```bash
# Dry-run (hanya tampilkan apa yang akan dihapus)
scripts/cleanup-storage.sh --dry-run
scripts/cleanup-compute.sh --dry-run
scripts/cleanup-network.sh --dry-run

# Eksekusi
sudo scripts/cleanup-all.sh --force
sudo scripts/cleanup-all.sh --force --with-volumes   # + hapus Docker volumes

# Reset lab tanpa reinstall node
make cleanup-all
make lab-setup
```

Cleanup hanya menarget resource dengan marker project (`cloudx-`, `test-`, `tap-`). Bridge `br-int`/`br-ex` tidak dihapus kecuali dengan flag `--remove-bridges`.

---

## 📚 Dokumentasi

- [docs/real-vm.md](docs/real-vm.md)
- [docs/flavor.md](docs/flavor.md)
- [docs/ipam.md](docs/ipam.md)
- [docs/ovn-dhcp.md](docs/ovn-dhcp.md)
- [docs/provider-network.md](docs/provider-network.md)
- [docs/ovn-localnet.md](docs/ovn-localnet.md)
- [docs/br-ex.md](docs/br-ex.md)
- [docs/metadata-service.md](docs/metadata-service.md)
- [docs/metadata-proxy.md](docs/metadata-proxy.md)
- [docs/floating-ip.md](docs/floating-ip.md)
- [docs/security-group.md](docs/security-group.md)
- [docs/ovn-acl.md](docs/ovn-acl.md)
- [docs/boot-from-volume.md](docs/boot-from-volume.md)
- [docs/snapshot.md](docs/snapshot.md)
- [docs/multi-network.md](docs/multi-network.md)
- [docs/placement-lite.md](docs/placement-lite.md)
- [docs/console.md](docs/console.md)
- [docs/libvirt.md](docs/libvirt.md)
- [docs/cloud-init.md](docs/cloud-init.md)
- [docs/cleanup.md](docs/cleanup.md)
- [docs/troubleshooting.md](docs/troubleshooting.md)
- [docs/troubleshooting-metadata.md](docs/troubleshooting-metadata.md)
- [docs/troubleshooting-security-group.md](docs/troubleshooting-security-group.md)
