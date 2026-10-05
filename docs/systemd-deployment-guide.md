# 📖 Panduan Deployment Native Systemd & Konfigurasi `/etc/cloudx/`

Dokumen ini menjelaskan tata cara deployment, konfigurasi, dan pengoperasian platform **CloudX** secara native di atas OS Linux menggunakan **Systemd Services** dan berkas konfigurasi baku per service di `/etc/cloudx/<service>/<service>.conf`.

---

## 🏗️ Arsitektur Deployment Native Systemd

Berbeda dengan arsitektur berbasis Docker Container, deployment native menjalankan seluruh microservice CloudX langsung di atas sistem operasi host:

- **Process Manager**: Systemd (`/etc/systemd/system/cloudx-*.service`)
- **WSGI/ASGI Engine**: Gunicorn dengan worker `uvicorn.workers.UvicornWorker`
- **User Runtime**: Dedicated system user `cloudx` (akses terbatas & terisolasi)
- **Virtual Environment**: `/opt/cloudx/venv`
- **Struktur Konfigurasi**: `/etc/cloudx/<service>/<service>.conf` (Format INI)
- **Logging**: Systemd Journald (`journalctl -u cloudx-<service>`)

---

## 🛠️ Prasyarat Sistem Host

Sebelum melakukan instalasi service CloudX, pastikan paket infrastruktur pendukung telah terpasang di OS host (Ubuntu 22.04 / 24.04 LTS):

```bash
# 1. Update paket & pasang dependencies utama
sudo apt update
sudo apt install -y python3-venv python3-pip postgresql postgresql-contrib \
                    rabbitmq-server ovn-central ovn-host openvswitch-switch \
                    libvirt-daemon-system libvirt-clients qemu-kvm
```

---

## 🚀 Langkah Instalasi Otomatis (`scripts/install-systemd.sh`)

CloudX menyediakan script installer otomatis untuk menyiapkan seluruh lingkungan systemd:

```bash
# Jalankan installer dengan hak akses sudo/root
sudo ./scripts/install-systemd.sh
```

### Apa yang dilakukan oleh `scripts/install-systemd.sh`?
1. Membuat user & group sistem `cloudx` (`useradd -r -s /sbin/nologin cloudx`).
2. Membuat direktori konfigurasi `/etc/cloudx/` dan direktori data `/var/lib/cloudx/`.
3. Menyalin berkas sampel konfigurasi `.conf.sample` ke `/etc/cloudx/<service>/<service>.conf` (jika belum ada).
4. Menyiapkan Python Virtual Environment di `/opt/cloudx/venv` dan memasang pustaka pendukung (`gunicorn`, `uvicorn`, `psycopg2-binary`, `sqlalchemy`, dll.).
5. Menyalin berkas unit systemd ke `/etc/systemd/system/cloudx-*.service`.
6. Menjalankan `systemctl daemon-reload`.

---

## ⚙️ Manajemen Berkas Konfigurasi (`/etc/cloudx/`)

Setiap microservice membaca konfigurasi dari berkas baku di `/etc/cloudx/<service>/<service>.conf`. Format berkas ini menggunakan standar INI yang intuitif.

### Contoh: `/etc/cloudx/compute-api/compute-api.conf`

```ini
[DEFAULT]
host = 127.0.0.1
port = 8003
log_level = info
workers = 4

[database]
url = postgresql+psycopg2://mini:mini@127.0.0.1:5432/cloudx

[transport]
rabbitmq_url = amqp://mini:mini@127.0.0.1:5672/

[service_urls]
auth_url = http://127.0.0.1:8005
placement_url = http://127.0.0.1:8006
networking_url = http://127.0.0.1:8001
block_store_url = http://127.0.0.1:8004
image_url = http://127.0.0.1:8002
```

### Struktur Direktori Konfigurasi Lengkap

```text
/etc/cloudx/
├── auth-api/
│   └── auth-api.conf
├── compute-api/
│   └── compute-api.conf
├── networking-api/
│   └── networking-api.conf
├── image-api/
│   └── image-api.conf
├── block-store-api/
│   └── block-store-api.conf
├── placement-api/
│   └── placement-api.conf
├── key-manager-api/
│   └── key-manager-api.conf
├── k8s-engine-api/
│   └── k8s-engine-api.conf
├── telemetry-api/
│   └── telemetry-api.conf
├── billing-api/
│   └── billing-api.conf
├── event-api/
│   └── event-api.conf
├── orchestration-api/
│   └── orchestration-api.conf
├── load-balancer-api/
│   └── load-balancer-api.conf
└── object-store-api/
    └── object-store-api.conf
```

---

## 📡 Mengaktifkan & Menjalankan Service

Setelah instalasi dan penyesuaian konfigurasi selesai, aktifkan seluruh service menggunakan `systemctl`:

```bash
# Aktifkan dan jalankan seluruh service CloudX sekaligus
sudo systemctl enable --now cloudx-*

# Atau aktifkan per service spesifik
sudo systemctl enable --now cloudx-compute-api
sudo systemctl enable --now cloudx-networking-api
sudo systemctl enable --now cloudx-k8s-engine-api
```

---

## 📊 Monitoring & Troubleshooting Service

### 1. Memeriksa Status Service via Systemctl
```bash
# Cek status seluruh service cloudx
systemctl status 'cloudx-*' --no-pager

# Cek status service spesifik
systemctl status cloudx-compute-api
```

### 2. Membaca Log Systemd via Journalctl
```bash
# Tail log real-time service compute-api
journalctl -u cloudx-compute-api -f

# Lihat 100 baris log terakhir service networking-api
journalctl -u cloudx-networking-api -n 100 --no-pager
```

### 3. Menggunakan CloudX CLI (`cloudx service`)
CloudX menyediakan fitur manajemen service terintegrasi langsung melalui CLI:

```bash
# List seluruh service CloudX
cloudx service list

# Cek status service
cloudx service status compute-api

# Restart service
cloudx service restart compute-api

# Tail log service
cloudx service logs compute-api -f
```
