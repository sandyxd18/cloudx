# 📖 Panduan Pengguna CloudX CLI & Fitur Service

Dokumen ini memberikan panduan lengkap penggunaan **CloudX Unified CLI** (`cloudx`) untuk mengelola seluruh resource Cloud Infrastructure (IaaS) dan Platform Services (PaaS) pada platform CloudX.

---

## 🔑 1. Otentikasi & Konfigurasi CLI

Sebelum menjalankan perintah CloudX CLI, lakukan konfigurasi kredensial otentikasi awal:

```bash
# Konfigurasi interaktif credentials
cloudx auth configure

# Atau atur via Environment Variables
export CLOUDX_ACCESS_KEY_ID="admin-key"
export CLOUDX_SECRET_ACCESS_KEY="admin-secret"
```

---

## 🛠️ 2. Cheat-Sheet Perintah Per Service

### 🆔 Identity & Quota (`cloudx auth`, `quota`, `audit`)
```bash
# Kelola User & Project
cloudx auth project-list
cloudx auth user-list

# Manajemen Kuota Tenant/Project
cloudx quota show admin
cloudx quota update admin --instances 20 --cores 40 --ram 81920 --gigabytes 500

# Audit Logging Sistem
cloudx audit list
```

### 💻 Compute Engine (`cloudx server`, `flavor`, `keypair`)
```bash
# Manajemen SSH Keypair
cloudx keypair create my-key
cloudx keypair list

# Manajemen Instance VM
cloudx server create web-server-01 --flavor m1.small --image img-ubuntu-2204 --network net-prod-01 --key-name my-key
cloudx server list
cloudx server show <server_id>
cloudx server console <server_id>
cloudx server delete <server_id>

# Image Prefetching ke Local Compute Node
cloudx server prefetch-image img-ubuntu-2204
```

### 🌐 Virtual Network & Security (`cloudx network`, `router`, `floating-ip`, `security-group`, `vpn`, `vpc-peering`)
```bash
# Networking Base (OVN)
cloudx network create net-prod-01 --cidr 10.0.1.0/24
cloudx router create router-gateway-01
cloudx floating-ip allocate
cloudx floating-ip associate <fip_id> <port_id>

# Security Group Presets (SSH, Web, DB, Ping, All)
cloudx security-group apply-preset default --preset web
cloudx security-group apply-preset default --preset ssh

# Cross-VPC Peering & BGP
cloudx vpc-peering create peer-prod-to-dev --router-a router-prod --router-b router-dev
cloudx bgp-peer list

# IPsec Site-to-Site VPNaaS
cloudx vpn ikepolicy create ike-policy-01 --auth-algo sha256 --enc-algo aes-256
cloudx vpn ipsecpolicy create ipsec-policy-01 --auth-algo sha256 --enc-algo aes-256
cloudx vpn connection create site-conn-branch1 \
    --router router-gateway-01 \
    --peer-address 198.51.100.20 \
    --peer-id 198.51.100.20 \
    --peer-cidr 10.200.0.0/16 \
    --psk my-secret-psk-key \
    --ikepolicy <ike_id> \
    --ipsecpolicy <ipsec_id>
cloudx vpn connection config <connection_id>
```

### 💾 Block Storage & DR (`cloudx volume`, `snapshot`, `volume-type`)
```bash
# Manajemen Volume Disk (LVM/Ceph)
cloudx volume create data-vol-01 --size 20 --type gp2
cloudx volume list
cloudx volume attach <volume_id> <server_id>

# Scheduled Volume Snapshots & Auto-Retention
cloudx volume snapshot-schedule-create data-vol-01 --cron "0 2 * * *" --retention 7

# Asynchronous Volume Replication & DR (Disaster Recovery)
cloudx volume enable-replication <volume_id> --target dr-sec-datacenter
cloudx volume sync-replication <volume_id>
cloudx volume failover <volume_id>
cloudx volume disable-replication <volume_id>
```

### 🔐 Secret & Certificate Vault (`cloudx secret`, `secret-container`)
```bash
# Key & Secret Management (Barbican-like AES-128-CBC Fernet)
cloudx secret create luks-storage-key --payload "my-super-secret-passphrase-999" --type symmetric
cloudx secret list
cloudx secret show <secret_id>

# Secret Container Bundle
cloudx secret-container create cert-bundle-prod --type certificate --secret-ref <secret_id>
```

### ☸️ Managed Kubernetes Engine (`cloudx k8s-template`, `cluster`)
```bash
# Cluster Template (K3s / RKE2)
cloudx k8s-template create k3s-v1.30-default \
    --image img-ubuntu-2204 \
    --k8s-version v1.30.2+k3s1 \
    --master-flavor m1.medium \
    --worker-flavor m1.small

# Provisioning Kubernetes Cluster
cloudx cluster create prod-k8s-cluster \
    --template <template_id> \
    --network net-prod-01 \
    --masters 1 \
    --workers 3

# Scaling Worker Nodes & Kubeconfig Download
cloudx cluster scale <cluster_id> --workers 5
cloudx cluster kubeconfig <cluster_id> > ~/.kube/config
```

### 🔄 Auto-scaling & Load Balancer (`cloudx scaling-group`, `load-balancer`)
```bash
# Heat-like Dynamic Auto-Scaling Group
cloudx scaling-group create web-autoscale-grp \
    --min-size 2 \
    --max-size 10 \
    --target-cpu 75 \
    --flavor m1.small \
    --image img-ubuntu-2204 \
    --network net-prod-01

# Octavia-like Load Balancer (L4 NLB / L7 ALB)
cloudx load-balancer create lb-web-frontend --vip-network-id net-prod-01
cloudx load-balancer list
```

### ⚙️ System Service Lifecycle (`cloudx service`)
```bash
# List & Monitor Systemd Daemon Services
cloudx service list
cloudx service status compute-api
cloudx service restart compute-api
cloudx service logs compute-api -f
```

---

## 🎯 3. Skenario End-to-End: Deployment Web Application + Storage + K8s

Berikut adalah langkah-langkah praktis membuat infrastruktur lengkap di CloudX:

```bash
# Step 1: Buat Jaringan & Security Group Preset
cloudx network create net-app-01 --cidr 10.10.0.0/24
cloudx security-group apply-preset default --preset web
cloudx security-group apply-preset default --preset ssh

# Step 2: Buat Disk Volume & Spawn VM
cloudx volume create db-volume-01 --size 50
cloudx server create app-server-01 --flavor m1.medium --image img-ubuntu-2204 --network net-app-01
cloudx volume attach <volume_id> <server_id>

# Step 3: Alokasikan Floating IP untuk Akses Publik
cloudx floating-ip allocate
cloudx floating-ip associate <fip_id> <port_id>

# Step 4: Deploy Kubernetes Cluster untuk Microservices Container
cloudx k8s-template create tpl-k8s-prod --image img-ubuntu-2204 --master-flavor m1.medium --worker-flavor m1.small
cloudx cluster create k8s-app-cluster --template <template_id> --network net-app-01 --workers 3
cloudx cluster kubeconfig <cluster_id> > ~/.kube/config
kubectl get nodes
```
