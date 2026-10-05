# CloudX — Private Cloud Platform

**CloudX** is a modular, adaptive private cloud platform built on native Linux (systemd) with a modern enterprise cloud architecture. It uses **libvirt + QEMU/KVM** for virtualization, **OVN/Open vSwitch** for software-defined networking, and **RabbitMQ** for asynchronous messaging between components. It ships with the **CloudX CLI** and an interactive **Web Dashboard** built on the AWS Cloudscape Design System.

---

## 🏛️ Architecture & Core Services

CloudX is a set of distributed microservices that can run in All-in-One or Multi-Node mode (Control Plane & Compute):

* **Auth (`auth-api`)**: Identity, project, user, role and credential management (Identity Service).
* **Compute (`compute-api`, `compute-worker`, `conductor`)**: VM lifecycle, flavors, keypairs, server groups and affinity policies (Compute Service).
* **Networking (`networking-api`)**: L2/L3 networks, subnets, router gateways, floating IPs, security groups, VPC peering, BGP and IPSec VPN (Networking Service).
* **Storage (`block-store-api`, `block-store-worker`)**: Persistent block storage, volume types (LVM / Ceph RBD), snapshots and full volume backups (Block Storage Service).
* **Image (`image-api`, `image-worker`)**: Repository of VM operating system images (qcow2/raw) with a local-file or MinIO S3 backend (Image Service).
* **Metadata & Placement (`metadata-api`, `placement-api`)**: Cloud-init metadata service (`169.254.169.254`) and capacity allocation tracking for compute nodes.
* **Orchestration & Containers (`orchestration-api`, `k8s_engine`)**: Metric-driven auto-scaling groups and a managed Kubernetes engine (K3s).
* **Security & Observability (`key_manager`, `telemetry-api`, `billing-api`, `event-api`)**: Integrated secret vault, utilization metrics, billing/invoicing and audit event logging.
* **Dashboard (`dashboard`)**: Modern React + Cloudscape web UI, connected through a unified reverse proxy.

---

## 💻 CloudX CLI Preview

CloudX can be managed efficiently from the command line (`cloudx`).

Use `-h` or `--help` on the root command or on any subgroup to see the full list of options and parameters:

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

To get help for a specific subcommand:
```bash
# Compute VM commands
cloudx server -h

# Networking commands
cloudx network --help

# Kubernetes cluster commands
cloudx cluster -h
```

---

## 🚀 Setup & Full Guides

Detailed instructions on installing prerequisites, configuring the host-only/provider network, and running CloudX on servers with **systemd** are in:

👉 **[SETUP.md — Server Deployment & Configuration Guide](./SETUP.md)**

Additional documentation:
* **[Native systemd Deployment Guide](./docs/systemd-deployment-guide.md)**
* **[Multi-Node Guide (Control Plane + Compute)](./docs/multi-node-deployment.md)**
* **[Complete CLI User Guide](./docs/user-guide-cli.md)**
