# 📖 Panduan Deployment & Pengetesan Multi-Node (2 VirtualBox VMs)

Dokumen ini memberikan panduan langkah demi langkah untuk menginstalasi, mengonfigurasi, dan menguji platform **CloudX** dalam arsitektur **Multi-Node Terpisah** (1 Control Plane Node + 1 Compute Worker Node) di atas lingkungan simulasi **VirtualBox**.

---

## 🏗️ 1. Arsitektur Multi-Node (2 Virtual Machines)

Dalam deployment multi-node, tugas API & Database dipisahkan secara fisik dari pemrosesan workload virtual machine (hypervisor):

```text
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                   HOST LAPTOP / PHYSICAL OS                                      │
│                                                                                                  │
│       Host-Only Network (vboxnet0)          Bridged Network              NAT Gateway             │
│            Subnet: 192.168.10.0/24         (Physical LAN Network)        (VirtualBox NAT)          │
└──────────────────┬─────────────────────────────────┬──────────────────────────┬──────────────────┘
                   │                                 │                          │
        ┌──────────┴──────────────┐                  │                 ┌────────┴─────────┐
        │                         │                  │                 │                  │
        ▼                         ▼                  ▼                 ▼                  ▼
┌───────────────────────────────┐       ┌──────────────────────────────────────────────────┐
│   VM 1: CONTROL PLANE NODE    │       │            VM 2: COMPUTE WORKER NODE             │
│  (IP Mgmt: 192.168.10.10)     │       │            (IP Mgmt: 192.168.10.11)              │
│                               │       │                                                  │
│  ┌─────────────────────────┐  │       │  ┌────────────────────────────────────────────┐  │
│  │ Core Infrastructure     │  │       │  │ Hypervisor & Networking Daemon             │  │
│  │  - Postgres DB  :5432   │  │       │  │  - QEMU / KVM + libvirtd                  │  │
│  │  - RabbitMQ     :5672   │  │       │  │  - Open vSwitch (OVS)                     │  │
│  │  - OVN Central DBs      │  │       │  │  - OVN Controller (bound to SB DB)        │  │
│  │    (NB: 6641, SB: 6642) │  │       │  └────────────────────────────────────────────┘  │
│  └─────────────────────────┘  │       │  ┌────────────────────────────────────────────┐  │
│  ┌─────────────────────────┐  │       │  │ Compute & Storage Worker Daemons           │  │
│  │ Control Plane REST APIs │  │◄──────┼──┼──- compute-worker (Compute Service)          │  │
│  │  - auth-api     :8005   │  │ AMQP  │  │  - block-store-worker (Block Store Worker)     │  │
│  │  - compute-api  :8003   │  │ & API │  └────────────────────────────────────────────┘  │
│  │  - placement    :8006   │  │       │  ┌────────────────────────────────────────────┐  │
│  │  - networking   :8001   │  │       │  │ Guest VMs (Libvirt QEMU Instances)        │  │
│  │  - Dashboard    :8080   │  │       │  │  - VM-1 (qcow2 overlay + config-drive)    │  │
│  └─────────────────────────┘  │       │  └────────────────────────────────────────────┘  │
└───────────────────────────────┘       └──────────────────────────────────────────────────┘
                │                                                │
                └────────────────── OVN Geneve Tunnel ───────────┘
                                       (Data Plane)
```

---

## 🌐 2. Konfigurasi VirtualBox Network Adapters

Sebelum menyalakan VM di VirtualBox, atur Network Adapters sesuai tabel berikut:

Subnet Host-Only Network VirtualBox (`vboxnet0`): **`192.168.10.0/24`**

### **VM 1: Control Plane Node (`cloudx-control-plane`)**
- **Adapter 1**: **Host-Only Network** (`vboxnet0`)
  - **IP Address**: `192.168.10.10`
  - **Fungsi**: Management & REST API Network (PostgreSQL `:5432`, RabbitMQ `:5672`, OVN Central DBs `:6641`/`:6642`, REST APIs `:8000`-`:8015`, Web Dashboard `:8080`).
- **Adapter 2**: **NAT**
  - **Fungsi**: Akses Internet OS Host VM 1 untuk update paket (`apt update`).

### **VM 2: Compute Worker Node (`cloudx-compute-01`)**
- **Adapter 1**: **Host-Only Network** (`vboxnet0`)
  - **IP Address**: `192.168.10.11`
  - **Fungsi**: Management & **OVN Data Plane Overlay Network** (Jalur OVN Geneve Tunnel antar-node dan komunikasi RPC `compute-worker` ke Control Plane).
- **Adapter 2**: **Bridged Adapter**
  - **Promiscuous Mode**: **Allow All** *(Set di VirtualBox Settings -> Network -> Advanced)*
  - **Fungsi**: Diikat ke Open vSwitch bridge `br-ex` sebagai Provider Interface (`PROVIDER_INTERFACE`) untuk alokasi Floating IP & routing keluar Guest VM.
- **Adapter 3**: **NAT**
  - **Fungsi**: **Sumber Internet Utama Host OS VM 2** (`apt update`, download OS base image).

---

## 🛠️ 3. Langkah-Langkah Deployment

### **Langkah 1: Setup Control Plane Node (VM 1)**

1. Cloning repositori CloudX di VM 1:
   ```bash
   git clone https://github.com/your-org/cloudx.git
   cd cloudx
   ```

2. Pastikan service infrastruktur mengikat IP `0.0.0.0` / `192.168.10.10`:
   - Postgres: `/etc/postgresql/.../main/postgresql.conf` -> `listen_addresses = '*'`
   - RabbitMQ: `/etc/rabbitmq/rabbitmq.conf` -> `listeners.tcp.default = 5672`
   - OVN Central DB: `ovn-nbctl set-connection ptcp:6641:0.0.0.0` dan `ovn-sbctl set-connection ptcp:6642:0.0.0.0`

3. Jalankan Control Plane API Services:
   - **Opsi Docker Compose**:
     ```bash
     docker compose -f docker-compose.control-plane.yml up -d
     ```
   - **Opsi Native Systemd**:
     ```bash
     sudo ./scripts/install-systemd.sh
     sudo systemctl enable --now cloudx-*
     ```

4. Verifikasi status Control Plane di VM 1:
   ```bash
   curl http://192.168.10.10:8005/health
   curl http://192.168.10.10:8003/health
   ```

---

### **Langkah 2: Setup Compute Worker Node (VM 2)**

> **Secret bersama.** `/etc/cloudx/cloudx.env` (berisi `AUTH_SECRET_KEY`, `AUTH_ADMIN_PASSWORD`, `KEY_MANAGER_MASTER_KEY`) dibuat otomatis oleh `install-systemd.sh` di control plane. Salin file yang sama ke setiap node lain (path sama, mode `640`) **sebelum** menjalankan installer di node tersebut. Secret yang berbeda antar node membuat verifikasi token dan login service gagal.

1. Cloning repositori CloudX di VM 2:
   ```bash
   git clone https://github.com/your-org/cloudx.git
   cd cloudx
   ```

2. Jalankan script setup Compute Node otomatis:
   ```bash
   sudo CONTROLLER_IP=192.168.10.10 NODE_IP=192.168.10.11 PROVIDER_IF=enp0s8 ./scripts/setup-compute-node.sh
   ```

3. Jalankan Worker Daemons (`compute-worker` & `block-store-worker`):
   - **Opsi Docker Compose**:
     ```bash
     docker compose -f docker-compose.compute-node.yml up -d
     ```
   - **Opsi Native Systemd**:
     ```bash
     sudo systemctl enable --now cloudx-compute-worker
     ```

---

## 📊 4. Verifikasi & Testing Multi-Node

### **1. Memeriksa Heartbeat Worker di Placement API**
Jalankan perintah ini di VM 1 (Control Plane) atau dari Host laptop:

```bash
cloudx placement provider list
```

**Hasil yang Diharapkan**:
```text
+-------------------+--------------------------------------+
| Name              | UUID                                 |
+-------------------+--------------------------------------+
| cloudx-compute-01 | a1b2c3d4-e5f6-7890-abcd-1234567890ab |
+-------------------+--------------------------------------+
```

---

### **2. Memeriksa Status Tunnel OVN Geneve**
Jalankan perintah ini di VM 1 atau VM 2:

```bash
sudo ovn-sbctl show
```

**Hasil yang Diharapkan**:
Terdapat entri `Chassis` untuk `cloudx-compute-01` dengan `encap ip "192.168.10.11"`.

---

### **3. Pengujian E2E Otomatis (`scripts/test-multinode-vm.py`)**
Jalankan script pengujian multi-node dari VM 1 atau VM 2:

```bash
python3 scripts/test-multinode-vm.py --controller 192.168.10.10 --worker-node cloudx-compute-01
```

Script ini akan memverifikasi kesehatan seluruh REST API Control Plane, pendaftaran node di Placement API, serta konektivitas OVN chassis tunnel.
