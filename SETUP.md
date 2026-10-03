# CloudX Setup & Deployment Guide (Native Systemd)

Panduan ini menjelaskan langkah demi langkah untuk menginstalasi, mengonfigurasi, dan menjalankan seluruh microservices **CloudX** secara *native* pada server Linux menggunakan **Systemd Services**.

---

## 🖥️ 1. Kebutuhan Sistem & Spesifikasi Server

### Spesifikasi Minimum (Single-Node All-in-One Lab)
* **Sistem Operasi**: Ubuntu 22.04 LTS / 24.04 LTS atau Debian 12 (Kernel 5.15+)
* **CPU / Virtualisasi**: Minimal 4 Cores dengan dukungan **Hardware Virtualization / KVM** (pastikan `/dev/kvm` tersedia).
* **RAM**: Minimal 8 GB RAM (Direkomendasikan **16 GB RAM** untuk menjalankan lab KVM VM, OVN, dan full stack).
* **Penyimpanan**: Minimal 50 GB SSD free space + 1 disk/partisi dedicated (misal `/dev/sdb` min 20 GB) untuk LVM Volume Group (`vg-cloudx`).
* **Jaringan (Network Adapters)**:
  * **NIC 1 (`NETWORK_INTERFACE`)**: Internal/Management interface (akses API, SSH host, komunikasi service).
  * **NIC 2 (`PROVIDER_INTERFACE`)**: Eksternal/Provider interface (diikat ke `br-ex` OVS untuk Floating IP & akses internet VM).

---

## 📦 2. Persiapan Paket Dependensi di Host

Jalankan perintah berikut di server untuk menginstal dependensi infrastruktur utama:

```bash
sudo apt update && sudo apt install -y \
  python3-venv python3-pip python3-dev build-essential \
  postgresql postgresql-contrib memcached rabbitmq-server \
  openvswitch-switch ovn-central ovn-host \
  qemu-kvm libvirt-daemon-system libvirt-clients \
  curl git jq net-tools iptables
```

Tambahkan user operasional ke grup `libvirt` dan `kvm`:
```bash
sudo usermod -aG libvirt,kvm $USER
newgrp libvirt
```

---

## 🌐 3. Konfigurasi OVS / OVN & Provider Network

Siapkan bridge OVS dan mapping OVN untuk provider network:

```bash
# 1. Buat bridge OVS
sudo ovs-vsctl --may-exist add-br br-int -- set Bridge br-int fail-mode=secure
sudo ovs-vsctl --may-exist add-br br-ex

# 2. Hubungkan PROVIDER_INTERFACE ke br-ex (misal: eth1 / enp0s8)
# Ganti enp0s8 dengan interface provider Anda
sudo ip link set enp0s8 up
sudo ovs-vsctl --may-exist add-port br-ex enp0s8

# 3. Konfigurasi bridge-mapping OVN ke br-ex
sudo ovs-vsctl set open . external-ids:ovn-bridge-mappings=provider:br-ex
sudo ovs-vsctl set open . external-ids:ovn-remote=unix:/var/run/ovn/ovnsb_db.sock
sudo ovs-vsctl set open . external-ids:ovn-encap-type=geneve
sudo ovs-vsctl set open . external-ids:ovn-encap-ip=$(hostname -I | awk '{print $1}')
```

> **Catatan VirtualBox / Lab Lokal**: Jika menggunakan adapter Host-Only (misal `vboxnet1` dengan subnet `172.24.4.0/24`), aktifkan mode *promiscuous* (`nicpromisc allow-all`) dan jalankan NAT MASQUERADE di host fisik agar VM memiliki akses internet keluar:
> ```bash
> sudo ./scripts/setup-host-provider-nat.sh vboxnet1 172.24.4.0/24
> ```

---

## 💾 4. Setup LVM Storage untuk Block Storage

Siapkan Physical Volume dan Volume Group untuk Cinder-like block storage:

```bash
# Ganti /dev/sdb dengan disk target Anda
sudo pvcreate /dev/sdb
sudo vgcreate vg-cloudx /dev/sdb
```

---

## 🛠️ 5. Instalasi Lingkungan CloudX (Step-by-Step Tanpa Script)

Ikuti langkah-langkah di bawah ini secara manual untuk menyiapkan seluruh runtime lingkungan CloudX:

### Langkah 5.1 — Konfigurasi Resolusi DNS Lokal (`/etc/hosts`)
Seluruh microservice saling berkomunikasi secara modular menggunakan alias domain standar. Tambahkan alias berikut ke `/etc/hosts`:

```bash
sudo bash -c 'cat << "EOF" >> /etc/hosts
127.0.0.1 auth-api compute-api networking-api image-api block-store-api placement-api key-manager-api k8s-engine-api telemetry-api billing-api event-api orchestration-api load-balancer-api object-store-api metadata-api rabbitmq postgres
EOF'
```

### Langkah 5.2 — Menyiapkan Database PostgreSQL
CloudX menggunakan PostgreSQL untuk menyimpan state metadata resource.

1. Buka prompt PostgreSQL:
   ```bash
   sudo -u postgres psql
   ```
2. Buat user `mini` dan berikan hak akses:
   ```sql
   CREATE USER mini WITH PASSWORD 'mini' SUPERUSER;
   ```
3. Buat database untuk masing-masing layanan CloudX:
   ```sql
   -- Database Core Services
   CREATE DATABASE mini_openstack OWNER mini;

   -- Database Extended Services
   CREATE DATABASE cloudx_orchestration OWNER mini;
   CREATE DATABASE cloudx_events OWNER mini;
   CREATE DATABASE cloudx_billing OWNER mini;
   CREATE DATABASE cloudx_k8s_engine OWNER mini;
   CREATE DATABASE cloudx_key_manager OWNER mini;

   \q
   ```

### Langkah 5.3 — Menyiapkan Message Broker RabbitMQ
RabbitMQ digunakan untuk komunikasi asinkron RPC antar-API dan Workers.

1. Pastikan service RabbitMQ aktif:
   ```bash
   sudo systemctl enable --now rabbitmq-server
   ```
2. Tambahkan akun pengguna `mini` dengan password `mini`:
   ```bash
   sudo rabbitmqctl add_user mini mini
   sudo rabbitmqctl set_user_tags mini administrator
   sudo rabbitmqctl set_permissions -p / mini ".*" ".*" ".*"
   ```

### Langkah 5.4 — Membuat System User & Group `cloudx`
Layanan CloudX dijalankan di bawah user sistem dedicated `cloudx` untuk isolasi hak akses:

```bash
# Buat group dan system user cloudx
sudo groupadd -r cloudx || true
sudo useradd -r -g cloudx -d /opt/cloudx -s /sbin/nologin -c "CloudX Service User" cloudx || true

# Masukkan user cloudx ke dalam grup libvirt dan kvm
sudo usermod -aG libvirt,kvm cloudx
```

### Langkah 5.5 — Membuat Direktori Data Runtime
Siapkan direktori lokal untuk menampung *image VM*, *object storage*, dan *instance libvirt*:

```bash
sudo mkdir -p /var/lib/mini-openstack/images \
              /var/lib/mini-openstack/object-store \
              /var/lib/mini-openstack/compute

# Berikan kepemilikan direktori ke user cloudx
sudo chown -R cloudx:cloudx /var/lib/mini-openstack
sudo chmod -R 775 /var/lib/mini-openstack
```

### Langkah 5.6 — Ekstraksi Release Tarball ke `/opt/cloudx`
Dapatkan paket rilis CloudX (`cloudx-latest.tar.gz` yang dibuat via `make package` atau diunduh dari rilis resmi), lalu ekstrak ke direktori `/opt/cloudx`:

```bash
# 1. Buat direktori aplikasi
sudo mkdir -p /opt/cloudx

# 2. Ekstrak release tarball
# (Pastikan file cloudx-latest.tar.gz sudah berada di direktori saat ini)
sudo tar -xzf cloudx-latest.tar.gz -C /opt/cloudx/

# 3. Berikan kepemilikan direktori kepada user cloudx
sudo chown -R cloudx:cloudx /opt/cloudx
```

> **Catatan Membuat Tarball Sendiri (Sisi Developer)**:
> Jika Anda membangun tarball dari source code repositori ini, jalankan perintah berikut:
> ```bash
> make package
> # File tarball siap saji akan dibuat di: dist/cloudx-latest.tar.gz
> ```

### Langkah 5.7 — Membuat Python Virtual Environment & Instalasi Pustaka
Bangun lingkungan Python Virtualenv di `/opt/cloudx/venv` dan pasang seluruh pustaka dependensi runtime menggunakan berkas `requirements.txt` yang sudah disediakan di dalam paket tarball:

```bash
# 1. Inisialisasi virtualenv
sudo python3 -m venv /opt/cloudx/venv

# 2. Upgrade pip, setuptools, dan wheel
sudo /opt/cloudx/venv/bin/pip install --upgrade pip setuptools wheel

# 3. Pasang pustaka dependensi dari requirements.txt bawaan paket
sudo /opt/cloudx/venv/bin/pip install -r /opt/cloudx/requirements.txt
```

### Langkah 5.8 — Mendaftarkan Berkas Systemd Service Unit
Salin definisi unit service langsung dari folder `etc/systemd/` yang telah diekstrak di `/opt/cloudx`:

```bash
# 1. Salin seluruh unit service ke direktori systemd
sudo cp /opt/cloudx/etc/systemd/*.service /etc/systemd/system/

# 2. Muat ulang systemd manager configuration
sudo systemctl daemon-reload
```

---

## ⚙️ 6. Konfigurasi Layanan (`/etc/cloudx/`)

Setiap microservice CloudX membaca konfigurasinya dari file INI baku di `/etc/cloudx/<service>/<service>.conf`.

### Langkah 6.1 — Menyiapkan Direktori & Konfigurasi dari Paket Rilis
Salin folder template konfigurasi dari `/opt/cloudx/etc/cloudx` ke `/etc/cloudx` dan aktifkan konfigurasi default:

```bash
# 1. Salin direktori template konfigurasi
sudo cp -r /opt/cloudx/etc/cloudx /etc/

# 2. Buat file .conf aktif dari masing-masing file .conf.sample
for sample_file in /etc/cloudx/*/*.conf.sample; do
  sudo cp "$sample_file" "${sample_file%.sample}"
done
```

### Langkah 6.3 — Anatomi & Penjelasan Parameter Konfigurasi

Setiap berkas `.conf` memiliki struktur blok INI yang modular. Contoh pada `/etc/cloudx/compute-api/compute-api.conf`:

```ini
[DEFAULT]
# IP host dan port listening untuk REST API
host = 127.0.0.1
port = 8003
log_level = info
workers = 4

[database]
# Format: postgresql+psycopg2://<user>:<password>@<host>:<port>/<dbname>
url = postgresql+psycopg2://mini:mini@127.0.0.1:5432/mini_openstack

[transport]
# Format: amqp://<user>:<password>@<host>:<port>/<vhost>
rabbitmq_url = amqp://mini:mini@127.0.0.1:5672/

[service_urls]
auth_url = http://127.0.0.1:8005
placement_url = http://127.0.0.1:8006
networking_url = http://127.0.0.1:8001
block_store_url = http://127.0.0.1:8004
image_url = http://127.0.0.1:8002

[keystone_authtoken]
auth_url = http://127.0.0.1:8005
```

### Langkah 6.4 — Detail Penyesuaian Khusus per Layanan

Periksa dan sesuaikan opsi penting pada layanan spesifik berikut:

1. **`block-store-api/block-store-api.conf` & Worker**:
   Pastikan bagian `[storage]` mengarah ke Volume Group LVM yang dibuat di Langkah 4:
   ```ini
   [storage]
   volume_group = vg-cloudx
   ```
2. **`image-api/image-api.conf`**:
   Pastikan lokasi penyimpanan image sesuai dengan direktori di Langkah 5.5:
   ```ini
   [storage]
   image_directory = /var/lib/mini-openstack/images
   ```
3. **`networking-api/networking-api.conf`**:
   Pastikan integrasi OVN/OVS mengarah ke socket OVN Southbound yang aktif:
   ```ini
   [ovn]
   ovn_sb_connection = unix:/var/run/ovn/ovnsb_db.sock
   ovs_integration_bridge = br-int
   ovs_external_bridge = br-ex
   ```
4. **`key-manager-api/key-manager-api.conf`**:
   Pastikan URL database mengarah ke database `cloudx_key_manager`:
   ```ini
   [database]
   url = postgresql+psycopg2://mini:mini@127.0.0.1:5432/cloudx_key_manager
   ```

### Langkah 6.5 — Mengatur Hak Akses & Permission Berkas Konfigurasi
Karena berkas konfigurasi memuat kredensial database dan pesan, lindungi hak aksesnya agar hanya dapat dibaca oleh user `cloudx` dan `root`:

```bash
sudo chown -R cloudx:cloudx /etc/cloudx
sudo chmod 750 /etc/cloudx
sudo find /etc/cloudx -type f -name "*.conf" -exec chmod 640 {} +
```

---

## 🚦 7. Menjalankan & Mengelola Service

### Mengaktifkan dan Menjalankan Seluruh Service

Jalankan perintah berikut untuk mengaktifkan seluruh service CloudX agar otomatis berjalan saat booting:

```bash
sudo systemctl enable --now \
  cloudx-auth-api \
  cloudx-conductor \
  cloudx-compute-api \
  cloudx-compute-worker \
  cloudx-networking-api \
  cloudx-block-store-api \
  cloudx-block-store-worker \
  cloudx-image-api \
  cloudx-image-worker \
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

### Memeriksa Status Service

Gunakan `cloudx service` pada CLI atau `systemctl`:

```bash
# Menggunakan CLI CloudX
cloudx service list

# Menggunakan systemctl
systemctl status cloudx-compute-api.service
systemctl status cloudx-dashboard.service
```

### Memeriksa Log Service

Semua log service terintegrasi langsung dengan systemd Journal:

```bash
# Memantau log real-time compute API
sudo journalctl -u cloudx-compute-api -f

# Memantau log networking API
sudo journalctl -u cloudx-networking-api -f
```

---

## 🌐 8. Mengakses Web Dashboard

Setelah service `cloudx-dashboard` berjalan aktif, buka peramban web dan arahkan ke alamat server:

* **URL**: `http://<IP_SERVER>:8080/`
* **Default Login**:
  * **Username**: `admin`
  * **Password**: `admin123`

Dashboard menyajikan antarmuka visual penuh untuk mengelola VM, jaringan, volume storage, Kubernetes engine, key manager vault, telemetry metrics, hingga riwayat billing.

---

## 🛠️ 9. Konfigurasi CloudX CLI di Terminal

Untuk mengontrol CloudX melalui terminal lokal atau remote:

```bash
# 1. Masuk ke direktori CLI dan pasang tool
cd /opt/cloudx/cli
pip install -e .

# 2. Lakukan inisialisasi kredensial CLI
cloudx auth configure
# Masukkan Access Key ID, Secret Key, dan Default Project saat diminta
```

Uji koneksi CLI:
```bash
cloudx server list
cloudx network list
cloudx hypervisor list
```
