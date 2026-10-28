# 📖 Native systemd Deployment & `/etc/cloudx/` Configuration Guide

> **Tarball era.** The installation part below uses the release tarball and `scripts/install-systemd.sh`. For packages see [deb-install.md](deb-install.md). The layout of `/etc/cloudx/` applies to both.

This document describes how to deploy, configure, and operate the **CloudX** platform natively on Linux using **systemd services** and a standard per-service configuration file at `/etc/cloudx/<service>/<service>.conf`.

---

## 🏗️ Native systemd Deployment Architecture

Unlike a container-based architecture, the native deployment runs every CloudX microservice directly on the host operating system:

- **Process manager**: systemd (`/etc/systemd/system/cloudx-*.service`)
- **WSGI/ASGI engine**: Gunicorn with the `uvicorn.workers.UvicornWorker` worker class
- **Runtime user**: dedicated system user `cloudx` (restricted and isolated)
- **Virtual environment**: `/opt/cloudx/venv`
- **Configuration layout**: `/etc/cloudx/<service>/<service>.conf` (INI format)
- **Logging**: systemd journald (`journalctl -u cloudx-<service>`)

---

## 🛠️ Host System Prerequisites

Before installing the CloudX services, make sure the supporting infrastructure packages are installed on the host OS (Ubuntu 22.04 / 24.04 LTS):

```bash
# 1. Update packages & install the main dependencies
sudo apt update
sudo apt install -y python3-venv python3-pip postgresql postgresql-contrib \
                    rabbitmq-server ovn-central ovn-host openvswitch-switch \
                    libvirt-daemon-system libvirt-clients qemu-kvm
```

---

## 🚀 Automatic Installation (`scripts/install-systemd.sh`)

CloudX provides an installer script that prepares the whole systemd environment:

```bash
# Run the installer with sudo/root privileges
sudo ./scripts/install-systemd.sh
```

### What does `scripts/install-systemd.sh` do?
1. Creates the `cloudx` system user & group (`useradd -r -s /sbin/nologin cloudx`).
2. Creates the configuration directory `/etc/cloudx/` and the data directory `/var/lib/cloudx/`.
3. Copies the `.conf.sample` files to `/etc/cloudx/<service>/<service>.conf` (if they do not exist yet).
4. Sets up the Python virtual environment in `/opt/cloudx/venv` and installs the supporting libraries (`gunicorn`, `uvicorn`, `psycopg2-binary`, `sqlalchemy`, and so on).
5. Copies the systemd unit files to `/etc/systemd/system/cloudx-*.service`.
6. Runs `systemctl daemon-reload`.

---

## ⚙️ Managing Configuration Files (`/etc/cloudx/`)

Each microservice reads its configuration from the standard file `/etc/cloudx/<service>/<service>.conf`. The files use the plain INI format.

### Example: `/etc/cloudx/compute-api/compute-api.conf`

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

### Full Configuration Directory Layout

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

## 📡 Enabling & Starting Services

After installation and configuration are done, enable the services with `systemctl`:

```bash
# Enable and start all CloudX services at once
sudo systemctl enable --now cloudx-*

# Or enable specific services
sudo systemctl enable --now cloudx-compute-api
sudo systemctl enable --now cloudx-networking-api
sudo systemctl enable --now cloudx-k8s-engine-api
```

---

## 📊 Service Monitoring & Troubleshooting

### 1. Checking service status with systemctl
```bash
# Check the status of all cloudx services
systemctl status 'cloudx-*' --no-pager

# Check a specific service
systemctl status cloudx-compute-api
```

### 2. Reading systemd logs with journalctl
```bash
# Tail the compute-api log in real time
journalctl -u cloudx-compute-api -f

# Show the last 100 log lines of networking-api
journalctl -u cloudx-networking-api -n 100 --no-pager
```

### 3. Using the CloudX CLI (`cloudx service`)
CloudX provides built-in service management directly through the CLI:

```bash
# List all CloudX services
cloudx service list

# Check service status
cloudx service status compute-api

# Restart a service
cloudx service restart compute-api

# Tail service logs
cloudx service logs compute-api -f
```
