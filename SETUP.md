# CloudX Setup & Deployment Guide (Native Systemd)

Panduan komprehensif ini menjelaskan instalasi, konfigurasi, dan pengoperasian **CloudX** secara native menggunakan **Systemd Services**, baik untuk arsitektur **3-Node Production** (Controller, Compute, Storage) yang telah tervalidasi 100%, maupun untuk pengujian cepat **Single-Node All-in-One**.

---

## 🏗️ 1. Arsitektur Multi-Node Production (3-Node Topology)

Arsitektur produksi CloudX memisahkan fungsionalitas bidang kontrol (Control Plane), komputasi virtualisasi (Compute Hypervisor), dan penyimpanan data terdistribusi (Shared Storage):

```
                        ┌────────────────────────────────────────────────────────┐
                        │                 Management Network                     │
                        │                 192.168.10.0/24 (NIC 2)                │
                        └───────┬────────────────────┬───────────────────┬───────┘
                                │                    │                   │
                                ▼                    ▼                   ▼
                     ┌─────────────────────┐ ┌────────────────┐ ┌────────────────┐
                     │   CONTROLLER NODE   │ │  COMPUTE NODE  │ │  STORAGE NODE  │
                     │  192.168.10.10      │ │ 192.168.10.11  │ │ 192.168.10.12  │
                     │  (6 GB RAM, 2 vCPU) │ │ (6 GB RAM, 2C) │ │ (4 GB RAM, 2C) │
                     └──────────┬──────────┘ └───────┬────────┘ └────────┬───────┘
                                │                    │                   │
                                │                    │                   │
 [Layanan]                      ▼                    ▼                   ▼
 PostgreSQL 14 (Port 5432) ─────┘                    │                   │
 RabbitMQ AMQP (Port 5672) ─────┘                    │                   │
 OVN Central DB (6641, 6642) ───┘                    │                   │
 CloudX Core REST APIs ─────────┘                    │                   │
 CloudX Web Dashboard (:8080) ──┘                    │                   │
                                                     │                   │
 QEMU / KVM Hypervisor ──────────────────────────────┘                   │
 libvirtd & ovn-controller ──────────────────────────┘                   │
 cloudx-compute-worker ──────────────────────────────┘                   │
 cloudx-metadata-proxy ──────────────────────────────┘                   │
 OVS Bridges (br-int, br-ex) ────────────────────────┘                   │
                                                                         │
 Dedicated Data Disk (/dev/sdb 30GB) ────────────────────────────────────┘
 Shared NFS Server (/srv/cloudx/volumes) ────────────────────────────────┘
 cloudx-block-store-worker (Block Store Volume) ─────────────────────────┘
 MinIO Object Storage (Port 9000, 9001) ─────────────────────────────────┘
                                │                    │
                                └────── NFS Mount ───┘
                           (/var/lib/cloudx/volumes)
                                                     │
                                                     ▼
                                      ┌────────────────────────┐
                                      │ Provider / Public Net  │
                                      │ 172.24.4.0/24 (NIC 3)  │
                                      │ Floating IP & Egress   │
                                      └────────────────────────┘
```

### Alokasi Sumber Daya Node

| Peran Node | Hostname | IP Management | RAM | vCPU | Disk OS | Disk Data | Layanan Utama |
|---|---|---|---|---|---|---|---|
| **Controller** | `cloudx-control-plane` | `192.168.10.10` | 6 GB | 2 | 30 GB | - | PostgreSQL, RabbitMQ, OVN-Central, REST APIs, CloudX Web Dashboard |
| **Compute** | `cloudx-compute-01` | `192.168.10.11` | 6 GB | 2 | 30 GB | - | KVM/QEMU, Libvirt, OVS/OVN-Host, `compute-worker`, `metadata-proxy`, NFS Client |
| **Storage** | `cloudx-storage-01` | `192.168.10.12` | 4 GB | 2 | 30 GB | 30 GB (`/dev/sdb`) | NFS Kernel Server, `block-store-worker`, MinIO S3 Object Store |

---

## 🛠️ 2. Langkah Instalasi Node 1: Controller Node (`192.168.10.10`)

### 2.1 Paket Dependensi & Basis Data
Jalankan di node Controller:
```bash
sudo apt update && sudo apt install -y \
  python3-venv python3-pip python3-dev build-essential \
  postgresql postgresql-contrib memcached rabbitmq-server \
  openvswitch-switch ovn-central ovn-common \
  curl git jq net-tools nfs-common
```

### 2.2 Konfigurasi Remote Listen PostgreSQL
Izinkan node Compute dan Storage mengakses PostgreSQL:
```bash
# 1. Edit /etc/postgresql/14/main/postgresql.conf
sudo sed -i "s/#listen_addresses = 'localhost'/listen_addresses = '*'/g" /etc/postgresql/14/main/postgresql.conf

# 2. Izinkan subnet management di /etc/postgresql/14/main/pg_hba.conf
echo "host    all             all             192.168.10.0/24         md5" | sudo tee -a /etc/postgresql/14/main/pg_hba.conf

sudo systemctl restart postgresql
```

Buat user dan database CloudX (kredensial dan nama database dapat disesuaikan; sesuaikan nilai `DATABASE_URL` pada unit service jika mengubah kredensial):
```bash
sudo -u postgres psql << "EOF"
CREATE USER cloudx WITH PASSWORD 'cloudx' SUPERUSER;
CREATE DATABASE cloudx OWNER cloudx;
CREATE DATABASE cloudx_orchestration OWNER cloudx;
CREATE DATABASE cloudx_events OWNER cloudx;
CREATE DATABASE cloudx_billing OWNER cloudx;
CREATE DATABASE cloudx_k8s_engine OWNER cloudx;
CREATE DATABASE cloudx_key_manager OWNER cloudx;
EOF
```

### 2.3 Konfigurasi RabbitMQ Broker
Buat user dan hak akses pada RabbitMQ:
```bash
sudo systemctl enable --now rabbitmq-server
sudo rabbitmqctl add_user cloudx cloudx
sudo rabbitmqctl set_user_tags cloudx administrator
sudo rabbitmqctl set_permissions -p / cloudx ".*" ".*" ".*"
```

### 2.4 Konfigurasi OVN Central Listener
Izinkan `ovn-controller` dari node compute terhubung ke Southbound DB:
```bash
# Listen pada port TCP 6641 (Northbound) dan 6642 (Southbound)
sudo ovn-nbctl set-connection ptcp:6641:0.0.0.0
sudo ovn-sbctl set-connection ptcp:6642:0.0.0.0
```

### 2.5 Instalasi Rilis CloudX di Controller

Paket rilis rilis CloudX dapat diunduh langsung dari **GitHub Releases** (untuk server dengan akses internet) atau melalui gateway HTTP Controller (untuk lingkungan lab terisolasi).

```bash
# Ganti v0.7.0 dengan versi rilis yang diinginkan
curl -sSL -L https://github.com/sandyxd18/cloudx/releases/download/v0.7.0/cloudx-0.7.0.tar.gz -o cloudx-latest.tar.gz
```

#### Ekstraksi dan Pemasangan:
```bash
# 1. Buat user & group sistem 'cloudx' serta direktori kerja
sudo groupadd -r cloudx 2>/dev/null || true
sudo useradd -r -g cloudx -d /opt/cloudx -s /sbin/nologin -c 'CloudX Service User' cloudx 2>/dev/null || true
sudo mkdir -p /opt/cloudx /var/lib/cloudx/images /var/lib/cloudx/volumes /var/lib/cloudx/instances /etc/cloudx

# 2. Ekstrak paket rilis
sudo tar -xzf cloudx-latest.tar.gz -C /opt/cloudx/

# 3. Buat venv Python & pasang dependensi
sudo python3 -m venv /opt/cloudx/venv
sudo /opt/cloudx/venv/bin/pip install --upgrade pip
sudo /opt/cloudx/venv/bin/pip install -r /opt/cloudx/requirements.txt
sudo /opt/cloudx/venv/bin/pip install -e /opt/cloudx/cli/ --no-deps
# Library bersama 'shared' + perintah cloudx-db-migrate
sudo /opt/cloudx/venv/bin/pip install /opt/cloudx/services --no-deps
sudo ln -sfn /opt/cloudx/venv/bin/cloudx /usr/local/bin/cloudx

# 4. Pasang unit service systemd & atur hak milik direktori
sudo cp /opt/cloudx/etc/systemd/*.service /etc/systemd/system/
sudo chown -R cloudx:cloudx /opt/cloudx /var/lib/cloudx /etc/cloudx
sudo systemctl daemon-reload

# 5. (Controller) Buat secret bersama. Salin file yang SAMA ke setiap node lain yang menjalankan service API
sudo install -m 640 -o root -g cloudx /dev/null /etc/cloudx/cloudx.env
{ echo "AUTH_SECRET_KEY=$(openssl rand -hex 32)"; echo "AUTH_ADMIN_PASSWORD=$(openssl rand -base64 18 | tr -d '/+=')"; echo "KEY_MANAGER_MASTER_KEY=$(openssl rand -hex 32)"; } | sudo tee /etc/cloudx/cloudx.env >/dev/null

# 6. (Controller) Buat schema database semua service. Aman diulang; wajib sebelum service dijalankan
sudo CLOUDX_SERVICES_DIR=/opt/cloudx/services /opt/cloudx/venv/bin/cloudx-db-migrate
```

Aktifkan Control Plane Services di Controller:
```bash
sudo systemctl enable --now \
  cloudx-auth-api \
  cloudx-conductor \
  cloudx-compute-api \
  cloudx-networking-api \
  cloudx-block-store-api \
  cloudx-image-api \
  cloudx-placement-api \
  cloudx-metadata-api \
  cloudx-orchestration-api \
  cloudx-key-manager-api \
  cloudx-k8s-engine-api \
  cloudx-telemetry-api \
  cloudx-billing-api \
  cloudx-event-api \
  cloudx-dashboard
```

### 2.6 Menentukan Kata Sandi (Password) User Admin
Akun administrator (`admin`) dibuat otomatis oleh `cloudx-auth-api` saat pertama kali berjalan. Kata sandinya **tidak punya nilai bawaan**: nilainya diambil dari `AUTH_ADMIN_PASSWORD` di `/etc/cloudx/cloudx.env`, yang dibuat acak oleh `scripts/install-systemd.sh` (atau oleh langkah 5 di atas).

#### Opsi A: Melihat atau menentukan password admin awal
```bash
sudo grep AUTH_ADMIN_PASSWORD /etc/cloudx/cloudx.env     # lihat password saat ini
sudoedit /etc/cloudx/cloudx.env                          # (opsional) ganti nilainya, lalu:
sudo systemctl restart cloudx-auth-api
```
`auth-api` meng-hash password itu (bcrypt) dan menyetelnya sebagai password akun `admin` **setiap kali restart**. Karena itu password yang diubah lewat CLI/Dashboard (Opsi B) akan tertimpa saat restart, kecuali nilai di `cloudx.env` ikut diubah.

#### Opsi B: Mengubah Password Admin Kapan Saja Setelah Sistem Berjalan (Runtime)
Jika sistem sudah terpasang dan berjalan, Anda dapat memperbarui kata sandi user admin kapan saja menggunakan salah satu metode berikut:

1. **Melalui CloudX CLI**:
   ```bash
   cloudx auth user set admin --password "KataSandiBaruAnda123!"
   ```
2. **Melalui CloudX Web Dashboard**:
   Buka dashboard di browser `http://192.168.10.10:8080/`, login, buka menu **Admin ➔ Users**, klik user **admin**, pilih **Edit User**, dan masukkan kata sandi baru.
3. **Melalui REST API langsung**:
   ```bash
   curl -X PUT http://127.0.0.1:8005/users/<ADMIN_USER_ID> \
     -H "X-Auth-Token: <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{"password": "KataSandiBaruAnda123!"}'
   ```

### 2.7 Menentukan Pool dan Inisialisasi Floating IP (Public / Provider Network)

Engine networking (OVN & OVS) yang sudah aktif di tingkat infrastruktur **belum otomatis membuat objek Logical Network** pada basis data CloudX. Pada instalasi baru, Anda perlu menginisialisasi objek Logical Network dan Subnet Publik pertama agar alokasi Floating IP dapat bekerja.

#### Langkah Standar Inisialisasi Provider Network & Floating IP (Via CloudX CLI)

Jalankan perintah berikut di Controller Node (atau node yang memiliki `cloudx` CLI terkonfigurasi):

```bash
# 1. Login sebagai Admin
cloudx auth login --username admin --password "<AUTH_ADMIN_PASSWORD dari /etc/cloudx/cloudx.env>" --auth-url http://127.0.0.1:8005

# 2. Buat External Provider Network (terhubung ke br-ex / physnet1 di NIC Provider)
cloudx network create public-net --external

# 3. Buat Subnet Publik dengan alokasi rentang Pool Floating IP
# (Sesuaikan CIDR, Gateway, dan Allocation Pool dengan subnet Provider Anda, misal 172.17.0.0/16 atau 172.24.4.0/24)
cloudx subnet create public-subnet \
  --network public-net \
  --cidr 172.17.0.0/16 \
  --gateway 172.17.0.1 \
  --allocation-start 172.17.0.100 \
  --allocation-end 172.17.0.200

# 4. Verifikasi objek jaringan & subnet telah terdaftar
cloudx network list
cloudx subnet list

# 5. Uji alokasi Floating IP pertama (IP akan otomatis diambil dari pool 172.17.0.100 - 172.17.0.200)
cloudx floating-ip create public-net
```

#### Opsi Lanjutan: Konfigurasi Default via Environment Variable (Systemd Override)
Jika Anda ingin menentukan nilai default pool jaringan provider di tingkat service agar otomatis digunakan saat auto-provisioning:
1. Buat file override konfigurasi systemd untuk `cloudx-networking-api`:
   ```bash
   sudo mkdir -p /etc/systemd/system/cloudx-networking-api.service.d
   sudo tee /etc/systemd/system/cloudx-networking-api.service.d/provider-network.conf << "EOF"
   [Service]
   Environment="PROVIDER_CIDR=172.17.0.0/16"
   Environment="PROVIDER_GATEWAY=172.17.0.1"
   Environment="PROVIDER_ALLOCATION_START=172.17.0.100"
   Environment="PROVIDER_ALLOCATION_END=172.17.0.200"
   EOF
   ```
2. Muat ulang systemd dan restart service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl restart cloudx-networking-api
   ```

---

## 💾 3. Langkah Instalasi Node 2: Storage Node (`192.168.10.12`)

### 3.1 Format Disk Dedicated & Mount Point
Storage node memanfaatkan disk dedicated `/dev/sdb` (30 GB) yang diekspor melalui NFS performa tinggi:
```bash
# 1. Format disk data ke filesystem ext4
sudo mkfs.ext4 -F /dev/sdb

# 2. Buat direktori mount dan tambahkan ke /etc/fstab
sudo mkdir -p /srv/cloudx
UUID=$(sudo blkid -s UUID -o value /dev/sdb)
echo "UUID=$UUID /srv/cloudx ext4 defaults 0 2" | sudo tee -a /etc/fstab
sudo mount -a

# 3. Buat subfolder volume dan perizinan
sudo mkdir -p /srv/cloudx/volumes /srv/cloudx/minio-data
sudo chmod 777 /srv/cloudx/volumes
sudo mkdir -p /var/lib/cloudx
sudo ln -sfn /srv/cloudx/volumes /var/lib/cloudx/volumes
```

### 3.2 Konfigurasi NFS Server
Pasang NFS Kernel Server dan bagikan direktori volume ke subnet management:
```bash
sudo apt update && sudo apt install -y nfs-kernel-server qemu-utils python3-venv python3-pip

# Konfigurasi export
echo "/srv/cloudx/volumes 192.168.10.0/24(rw,sync,no_subtree_check,no_root_squash)" | sudo tee /etc/exports
sudo exportfs -ra
sudo systemctl restart nfs-kernel-server
sudo systemctl enable nfs-kernel-server
```

### 3.3 Instalasi CloudX Block Store Worker di Storage Node

```bash
sudo groupadd -r cloudx 2>/dev/null || true
sudo useradd -r -g cloudx -d /opt/cloudx -s /sbin/nologin -c 'CloudX Service User' cloudx 2>/dev/null || true
sudo mkdir -p /opt/cloudx /var/lib/cloudx/volumes /etc/cloudx

curl -sSL -L https://github.com/sandyxd18/cloudx/releases/download/v0.7.0/cloudx-0.7.0.tar.gz -o /tmp/cloudx-latest.tar.gz

sudo tar -xzf /tmp/cloudx-latest.tar.gz -C /opt/cloudx/

sudo python3 -m venv /opt/cloudx/venv
sudo /opt/cloudx/venv/bin/pip install --upgrade pip
sudo /opt/cloudx/venv/bin/pip install -r /opt/cloudx/requirements.txt
sudo chown -R cloudx:cloudx /opt/cloudx /var/lib/cloudx /etc/cloudx
```

Konfigurasikan unit service `/etc/systemd/system/cloudx-block-store-worker.service`:
```ini
[Unit]
Description=CloudX Block Store Worker Service (Block Store Volume Worker)
After=network.target

[Service]
Environment=PYTHONPATH=/opt/cloudx/services
Environment=STORAGE_NFS_DRIVER=true
Environment=STORAGE_NFS_DIR=/var/lib/cloudx/volumes
Environment=IMAGE_API_URL=http://192.168.10.10:8002
Environment=RABBITMQ_URL=amqp://cloudx:cloudx@192.168.10.10:5672/
Environment=DATABASE_URL=postgresql+psycopg://cloudx:cloudx@192.168.10.10:5432/cloudx
Type=simple
User=root
Group=root
WorkingDirectory=/opt/cloudx/services/block_store_worker
ExecStart=/opt/cloudx/venv/bin/python3 -m app.main
Restart=always
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

Jalankan service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now cloudx-block-store-worker.service
```

---

## ⚡ 4. Langkah Instalasi Node 3: Compute Node (`192.168.10.11`)

### 4.1 Dependensi Virtualisasi & OVS/OVN
Jalankan di node Compute:
```bash
sudo apt update && sudo apt install -y \
  qemu-kvm libvirt-daemon-system libvirt-clients \
  openvswitch-switch ovn-host nfs-common qemu-utils \
  python3-venv python3-pip python3-dev
```

### 4.2 Mount Shared NFS Storage
Mount direktori volume dari Storage Node (`192.168.10.12`):
```bash
sudo mkdir -p /var/lib/cloudx/volumes
echo "192.168.10.12:/srv/cloudx/volumes /var/lib/cloudx/volumes nfs defaults,_netdev 0 0" | sudo tee -a /etc/fstab
sudo mount /var/lib/cloudx/volumes
```

### 4.3 Konfigurasi OVS, OVN Controller & Provider Bridge
Hubungkan compute node ke OVN Southbound Database di Controller:
```bash
# 1. Buat bridge integrasi dan provider
sudo ovs-vsctl --may-exist add-br br-int -- set Bridge br-int fail-mode=secure
sudo ovs-vsctl --may-exist add-br br-ex

# 2. Kaitkan interface provider (NIC 3, misal enp0s9) ke br-ex
sudo ip link set enp0s9 up
sudo ip link set br-ex up
sudo ovs-vsctl --may-exist add-port br-ex enp0s9

# 3. Atur konfigurasi OVN Controller
sudo ovs-vsctl set Open_vSwitch . \
  external_ids:system-id="cloudx-compute-01" \
  external_ids:hostname="cloudx-compute-01" \
  external_ids:ovn-remote="tcp:192.168.10.10:6642" \
  external_ids:ovn-encap-type="geneve" \
  external_ids:ovn-encap-ip="192.168.10.11" \
  external_ids:ovn-bridge-mappings="physnet1:br-ex" \
  external_ids:ovn-cms-options="enable-chassis-as-gw"

sudo systemctl restart ovn-controller
```

### 4.4 Instalasi & Menjalankan Worker Daemons di Compute Node

```bash
sudo groupadd -r cloudx 2>/dev/null || true
sudo useradd -r -g cloudx -d /opt/cloudx -s /sbin/nologin -c 'CloudX Service User' cloudx 2>/dev/null || true
sudo mkdir -p /opt/cloudx /var/lib/cloudx/instances /etc/cloudx

curl -sSL -L https://github.com/your-org/cloudx/releases/download/v0.7.0/cloudx-0.7.0.tar.gz -o /tmp/cloudx-latest.tar.gz

sudo tar -xzf /tmp/cloudx-latest.tar.gz -C /opt/cloudx/

sudo python3 -m venv /opt/cloudx/venv
sudo /opt/cloudx/venv/bin/pip install --upgrade pip
sudo /opt/cloudx/venv/bin/pip install -r /opt/cloudx/requirements.txt

# Pasang unit service & atur hak milik direktori
sudo cp /opt/cloudx/etc/systemd/*.service /etc/systemd/system/
sudo chown -R cloudx:cloudx /opt/cloudx /var/lib/cloudx /etc/cloudx
```

Konfigurasikan unit service `/etc/systemd/system/cloudx-compute-worker.service`:
```ini
[Unit]
Description=CloudX Compute Worker Service
After=network.target libvirtd.service ovs-vswitchd.service

[Service]
Environment=PYTHONPATH=/opt/cloudx/services
Environment=NODE_NAME=cloudx-compute-01
Environment=COMPUTE_HOST=cloudx-compute-01
Environment=RABBITMQ_URL=amqp://cloudx:cloudx@192.168.10.10:5672/
Environment=DATABASE_URL=postgresql+psycopg://cloudx:cloudx@192.168.10.10:5432/cloudx
Environment=PLACEMENT_API_URL=http://192.168.10.10:8006
Type=simple
User=root
Group=root
WorkingDirectory=/opt/cloudx/services/compute_worker
ExecStart=/opt/cloudx/venv/bin/python3 -m app.main
Restart=always
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

Jalankan worker daemons:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now cloudx-compute-worker.service cloudx-metadata-proxy.service
```

---

## ✅ 5. Verifikasi & Pengujian End-to-End Cluster 3-Node

Lakukan pengujian menyeluruh langsung dari CLI CloudX di Controller Node:

### 5.1 Cek Status Hypervisor & Komputasi
```bash
cloudx hypervisor list
```
*Output yang diharapkan:*
```
+-------------------+-------------------+----------+---------------+
| host              | name              | status   |   running_vms |
+===================+===================+==========+===============+
| cloudx-compute-01 | cloudx-compute-01 | active   |             0 |
+-------------------+-------------------+----------+---------------+
```

### 5.2 Pembuatan Volume di Storage Node
```bash
cloudx volume create vol-prod-data --size 2 --type nfs-storage
cloudx volume list
```
*Verifikasi fisik di Storage Node (`192.168.10.12`):*
```bash
ls -lh /srv/cloudx/volumes/
```

### 5.3 Peluncuran Virtual Machine di Compute Node
```bash
cloudx server create vm-app-01 --image cirros-0.6.2-x86_64 --flavor tiny --network private-net
cloudx server list
```
*Verifikasi domain libvirt di Compute Node (`192.168.10.11`):*
```bash
sudo virsh list
```

### 5.4 Hotplug Volume Attach Lintas Node
Kaitkan volume penyimpanan (Storage Node) ke VM yang sedang aktif (Compute Node):
```bash
cloudx volume attach vol-prod-data --server vm-app-01
```
*Periksa disk di hypervisor:*
```bash
sudo virsh domblklist cloudx-vm-app-01-<ID>
# Menampilkan sda (root disk qcow2) dan sdb (data disk qcow2 dari NFS)
```

### 5.5 Uji Asosiasi Floating IP & Akses Jaringan
Asosiasikan Floating IP publik dan lakukan uji konektivitas dari luar:
```bash
cloudx floating-ip associate 172.24.4.100 --server vm-app-01 --router public-router
ping -c 3 172.24.4.100
ssh cirros@172.24.4.100
```

---

## 💻 6. Panduan Lengkap Penggunaan CloudX CLI

CloudX CLI (`cloudx`) adalah antarmuka baris perintah terpadu untuk mengelola seluruh siklus hidup infrastruktur private cloud Anda dari terminal.

### 6.1 Instalasi & Setup CLI

#### Pada Node Controller
CLI sudah terpasang otomatis di `/usr/local/bin/cloudx` (tertaut ke `/opt/cloudx/venv/bin/cloudx`). Anda dapat langsung menggunakannya.

#### Pada Mesin Remote / Laptop Pengguna
Jika Anda ingin mengelola cluster CloudX dari komputer lokal atau workstation Anda:
```bash
# 1. Unduh repositori CloudX
git clone <URL_REPO_CLOUDX> cloudx
cd cloudx

# 2. Pasang package CLI ke virtualenv lokal
python3 -m venv .venv
source .venv/bin/activate
pip install -e cli/
```

---

### 6.2 Metode Autentikasi CLI (Pilih Salah Satu)

CloudX CLI mendukung tiga metode autentikasi yang fleksibel:

#### Metode 1: Login Interaktif (Sangat Direkomendasikan)
Perintah ini akan menanyakan kata sandi admin yang telah Anda tentukan, memverifikasi akun ke Auth API, dan secara otomatis membuat kredensial Access Key permanen di `~/.cloudx/credentials`:
```bash
cloudx auth login --username admin --auth-url http://192.168.10.10:8005
# Masukkan kata sandi admin Anda saat diminta
```
*Output berhasil:*
```
☁  cloudx auth login

Authenticating... ✓
✓ Successfully logged in as 'admin' in project 'admin'
✓ Credentials saved to ~/.cloudx/credentials
✓ Configuration saved to ~/.cloudx/config
```

#### Metode 2: Menggunakan Environment Variables (Cocok untuk Scripting / CI/CD)
Anda dapat mengekspor variabel lingkungan berikut di terminal atau skrip shell tanpa perlu memasukkan password secara manual:
```bash
export CLOUDX_AUTH_URL="http://192.168.10.10:8005"
export CLOUDX_USERNAME="admin"
export CLOUDX_PASSWORD="<Password_Admin_Anda>"
```
Setelah di-export, seluruh perintah `cloudx` akan langsung terautentikasi otomatis.

#### Metode 3: Konfigurasi Access Key Manual
Jika Anda memiliki Access Key ID & Secret Access Key yang digenerate dari Web Dashboard:
```bash
cloudx auth configure
# Masukkan CloudX Access Key ID
# Masukkan CloudX Secret Access Key
# Masukkan Default project (default: admin)
# Masukkan Output format (default: table)
```

---

### 6.3 Contoh Perintah Operasional CLI Sehari-Hari

#### A. Manajemen Compute (Virtual Machine)
```bash
# Melihat daftar flavor (ukuran vCPU / RAM)
cloudx flavor list

# Melihat daftar image OS yang tersedia
cloudx image list

# Membuat VM baru
cloudx server create vm-web-01 \
  --image cirros-0.6.2-x86_64 \
  --flavor tiny \
  --network private-net

# Melihat daftar instance VM
cloudx server list

# Melihat detail lengkap spesifikasi dan IP instance
cloudx server show vm-web-01

# Mengontrol status VM (Reboot, Stop, Start, Pause, Resume)
cloudx server reboot vm-web-01
cloudx server stop vm-web-01
cloudx server start vm-web-01

# Menghapus VM
cloudx server delete vm-web-01
```

#### B. Manajemen Penyimpanan (Block Storage Volume)
```bash
# Membuat volume storage baru (ukuran dalam GB)
cloudx volume create vol-data-01 --size 5 --type nfs-storage

# Melihat daftar volume
cloudx volume list

# Memasang volume ke VM yang sedang berjalan (Hotplug)
cloudx volume attach vol-data-01 --server vm-web-01

# Melepaskan volume dari VM
cloudx volume detach vol-data-01

# Membuat snapshot volume
cloudx snapshot create snap-vol-01 --volume vol-data-01
cloudx snapshot list
```

#### C. Manajemen Jaringan & IP Publik (Networking)
```bash
# Menampilkan jaringan virtual dan subnet
cloudx network list
cloudx subnet list

# Menampilkan router virtual dan routing gateway
cloudx router list

# Melihat daftar Floating IP publik
cloudx floating-ip list

# Mengasosiasikan Floating IP publik ke VM
cloudx floating-ip associate 172.24.4.100 --server vm-web-01 --router public-router

# Melepas asosiasi Floating IP
cloudx floating-ip disassociate 172.24.4.100

# Menampilkan aturan firewall (Security Groups)
cloudx security-group list
```

#### D. Manajemen Identitas & Pengguna (Auth & Identity)
```bash
# Menampilkan daftar pengguna dan proyek
cloudx auth user list
cloudx auth project list

# Membuat proyek/tenant baru
cloudx auth project create production --description "Production Workloads"

# Membuat pengguna baru
cloudx auth user create devuser \
  --email devuser@cloudx.local \
  --password "UserSecretPassword123!" \
  --project production \
  --role member

# Mengubah kata sandi pengguna
cloudx auth user set devuser --password "NewUserSecretPassword123!"
```

---

## 🌐 7. Mengakses CloudX Web Dashboard

Setelah seluruh service aktif:
* **URL**: `http://192.168.10.10:8080/`
* **Username**: `admin`
* **Password**: nilai `AUTH_ADMIN_PASSWORD` di `/etc/cloudx/cloudx.env` pada Controller (atau yang Anda tentukan sendiri, lihat 2.6)

Dashboard menyediakan visualisasi topologi cluster, utilisasi CPU/RAM compute nodes, kuota disk storage node, dan konsol VNC instance secara real-time.
