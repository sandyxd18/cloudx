# CloudX — Private Cloud Platform

**CloudX** adalah platform private cloud modular dan adaptif berbasis Linux native (Systemd) dengan arsitektur enterprise cloud modern. Menggunakan **libvirt + QEMU/KVM** untuk virtualisasi, **OVN/Open vSwitch** untuk software-defined networking, **RabbitMQ** untuk messaging asinkron antar-komponen, serta dilengkapi **CloudX CLI** dan **Web Dashboard** interaktif berbasis AWS Cloudscape Design System.

---

## 🏛️ Arsitektur & Layanan Inti

CloudX terdiri dari microservices terdistribusi yang dapat berjalan dalam mode All-in-One maupun Multi-Node (Control Plane & Compute):

* **Auth (`auth-api`)**: Manajemen otentikasi identitas, proyek, pengguna, peran (Identity Service), dan credentials.
* **Compute (`compute-api`, `compute-worker`, `conductor`)**: Manajemen siklus hidup VM, flavor, keypair, server groups, dan affinity policies (Compute Service).
* **Networking (`networking-api`)**: L2/L3 Network, subnet, router gateway, floating IP, security groups, VPC peering, BGP, dan IPSec VPN (Networking Service).
* **Storage (`block-store-api`, `block-store-worker`)**: Block storage persistent, volume types (LVM / Ceph RBD), snapshot, dan full volume backup (Block Storage Service).
* **Image (`image-api`, `image-worker`)**: Repositori sistem operasi VM berbasis qcow2/raw dengan backend local file atau MinIO S3 (Image Service).
* **Metadata & Placement (`metadata-api`, `placement-api`)**: Cloud-init metadata service (`169.254.169.254`) dan tracking alokasi kapasitas compute nodes.
* **Orchestration & Containers (`orchestration-api`, `k8s_engine`)**: Auto-scaling groups berbasis metrik utilisasi dan Managed Kubernetes Engine (K3s).
* **Security & Observability (`key_manager`, `telemetry-api`, `billing-api`, `event-api`)**: Secret vault terintegrasi, pengumpulan metrik utilisasi, billing/invoicing, dan audit event logging.
* **Dashboard (`dashboard`)**: Antarmuka web modern React + Cloudscape UI yang terhubung langsung melalui reverse proxy terpadu.

---

## 💻 CloudX CLI Preview

Pengelolaan CloudX dapat dilakukan secara efisien melalui antarmuka baris perintah (`cloudx`). 

Gunakan opsi `-h` atau `--help` pada root maupun subgrup perintah untuk melihat opsi dan parameter lengkap:

```bash
$ cloudx --help
Usage: cloudx [OPTIONS] COMMAND [ARGS]...

  CloudX unified CLI — manage your private cloud from the terminal.

Options:
  --profile TEXT       Credential profile to use (default: default)
  -f, --format [table|json|yaml]  Output format (default: table)
  -h, --help           Show this message and exit.

Commands:
  auth            Authentication and identity management (users, projects)
  server          Virtual machine compute lifecycle management
  flavor          Compute flavor management (vCPU, RAM, disk)
  keypair         SSH key pair management
  server-group    Server affinity/anti-affinity policy groups
  scaling-group   Auto-scaling group orchestration
  stack           Orchestration stack and resource management
  network         Software-defined networks
  subnet          IP subnet and IPAM management
  router          L3 router and external gateway routing
  floating-ip     Public floating IP addresses
  security-group  Firewall security groups and packet filter rules
  vpc-peering     VPC peering inter-router connections
  vpn             IPSec site-to-site VPN gateways & policies
  bgp-peer        BGP dynamic routing peer management
  image           Virtual machine operating system images
  volume          Block storage persistent volumes
  volume-type     Volume storage backend types & performance profiles
  backup          Full volume backups to object storage
  snapshot        Volume snapshots
  object-store    S3-compatible bucket and object storage
  secret          Key manager secret vault
  cluster         Managed Kubernetes cluster provisioning
  k8s-template    Kubernetes cluster version & node templates
  hypervisor      Inspect compute host nodes and physical capacity
  telemetry       Resource utilization samples and metrics
  billing         Usage metering, rating sheets, and invoices
  event           Centralized audit event logs
  service         Systemd services and daemon lifecycle
```

Untuk melihat bantuan pada sub-perintah tertentu:
```bash
# Bantuan perintah compute VM
cloudx server -h

# Bantuan perintah jaringan
cloudx network --help

# Bantuan perintah klaster Kubernetes
cloudx cluster -h
```

---

## 🚀 Panduan Setup & Panduan Lengkap

Instruksi mendalam mengenai instalasi prasyarat, konfigurasi jaringan host-only/provider, dan tata cara menjalankan CloudX di server menggunakan **Systemd** dapat dibaca pada:

👉 **[SETUP.md — Panduan Deployment & Konfigurasi Server](./SETUP.md)**

Dokumentasi tambahan:
* **[Panduan Deployment Native Systemd](./docs/systemd-deployment-guide.md)**
* **[Panduan Multi-Node (Control Plane + Compute)](./docs/multi-node-deployment.md)**
* **[Panduan Penggunaan CLI Lengkap](./docs/user-guide-cli.md)**
