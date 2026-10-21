# 📖 Multi-Node Deployment & Testing Guide (2 VirtualBox VMs)

This document is a step-by-step guide to installing, configuring, and testing the **CloudX** platform in a **separated multi-node** architecture (1 control plane node + 1 compute worker node) on a **VirtualBox** simulation environment.

---

## 🏗️ 1. Multi-Node Architecture (2 Virtual Machines)

In a multi-node deployment, the API & database duties are physically separated from the virtual machine workload processing (the hypervisor):

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

## 🌐 2. VirtualBox Network Adapter Configuration

Before powering on the VMs in VirtualBox, set the network adapters as in the following table:

VirtualBox Host-Only Network subnet (`vboxnet0`): **`192.168.10.0/24`**

### **VM 1: Control Plane Node (`cloudx-control-plane`)**
- **Adapter 1**: **Host-Only Network** (`vboxnet0`)
  - **IP address**: `192.168.10.10`
  - **Purpose**: Management & REST API network (PostgreSQL `:5432`, RabbitMQ `:5672`, OVN Central DBs `:6641`/`:6642`, REST APIs `:8000`-`:8015`, Web Dashboard `:8080`).
- **Adapter 2**: **NAT**
  - **Purpose**: Internet access for the VM 1 host OS for package updates (`apt update`).

### **VM 2: Compute Worker Node (`cloudx-compute-01`)**
- **Adapter 1**: **Host-Only Network** (`vboxnet0`)
  - **IP address**: `192.168.10.11`
  - **Purpose**: Management & **OVN data plane overlay network** (OVN Geneve tunnel between nodes and `compute-worker` RPC to the control plane).
- **Adapter 2**: **Bridged Adapter**
  - **Promiscuous mode**: **Allow All** *(set in VirtualBox Settings -> Network -> Advanced)*
  - **Purpose**: Bound to the Open vSwitch bridge `br-ex` as the provider interface (`PROVIDER_INTERFACE`) for floating IP allocation & outbound routing of guest VMs.
- **Adapter 3**: **NAT**
  - **Purpose**: **Main internet source for the VM 2 host OS** (`apt update`, downloading base OS images).

---

## 🛠️ 3. Deployment Steps

### **Step 1: Set up the Control Plane Node (VM 1)**

1. Clone the CloudX repository on VM 1:
   ```bash
   git clone https://github.com/your-org/cloudx.git
   cd cloudx
   ```

2. Make sure the infrastructure services bind to `0.0.0.0` / `192.168.10.10`:
   - Postgres: `/etc/postgresql/.../main/postgresql.conf` -> `listen_addresses = '*'`
   - RabbitMQ: `/etc/rabbitmq/rabbitmq.conf` -> `listeners.tcp.default = 5672`
   - OVN Central DB: `ovn-nbctl set-connection ptcp:6641:0.0.0.0` and `ovn-sbctl set-connection ptcp:6642:0.0.0.0`

3. Start the control plane API services (native systemd):
   ```bash
   sudo ./scripts/install-systemd.sh
   sudo systemctl enable --now cloudx-*
   ```

4. Verify the control plane status on VM 1:
   ```bash
   curl http://192.168.10.10:8005/health
   curl http://192.168.10.10:8003/health
   ```

---

### **Step 2: Set up the Compute Worker Node (VM 2)**

> **Shared secrets.** `/etc/cloudx/cloudx.env` (containing `AUTH_SECRET_KEY`, `AUTH_ADMIN_PASSWORD`, `KEY_MANAGER_MASTER_KEY`) is created automatically by `install-systemd.sh` on the control plane. Copy the same file to every other node (same path, mode `640`) **before** running the installer there. Different secrets across nodes make token verification and service login fail.

1. Clone the CloudX repository on VM 2:
   ```bash
   git clone https://github.com/your-org/cloudx.git
   cd cloudx
   ```

2. Run the automatic compute node setup script:
   ```bash
   sudo CONTROLLER_IP=192.168.10.10 NODE_IP=192.168.10.11 PROVIDER_IF=enp0s8 ./scripts/setup-compute-node.sh
   ```

3. Fill the service catalog with addresses the compute node can reach.

   `setup-compute-node.sh` writes only `auth_api_url` for services; the worker finds placement, networking, image and block-store through the catalog served by auth. Auth seeds every service with `http://127.0.0.1:<port>` at its first start, which on VM 2 points at VM 2 itself: the worker would fail with a plain `ConnectionError` and no compute host would appear in placement. On the control plane (VM 1), write the reachable URL of each service in `/etc/cloudx/auth-api/auth-api.conf`. The keys are the `url_key` column of `services/shared/services.py` (`placement_api_url`, `networking_api_url`, `image_api_url`, `block_store_api_url`, `compute_api_url`, `metadata_api_url`, `load_balancer_api_url`, `telemetry_api_url`, `ha_api_url`, and so on):
   ```ini
   [DEFAULT]
   placement_api_url = http://192.168.10.10:8006
   networking_api_url = http://192.168.10.10:8001
   image_api_url = http://192.168.10.10:8002
   block_store_api_url = http://192.168.10.10:8004
   compute_api_url = http://192.168.10.10:8003
   ```
   Then preview and apply the change (no restart of `cloudx-auth-api` is needed; the command reads the conf itself):
   ```bash
   sudo CLOUDX_SERVICES_DIR=/opt/cloudx/services /opt/cloudx/venv/bin/cloudx-manage catalog sync --dry-run   # read the + and ~ lines
   sudo CLOUDX_SERVICES_DIR=/opt/cloudx/services /opt/cloudx/venv/bin/cloudx-manage catalog sync
   ```
   `~` lines are expected: they replace the `127.0.0.1` entries seeded at first start. `cloudx-manage` is installed into the venv by `install-systemd.sh` (it pip-installs `/opt/cloudx/services`).

   Alternative that still works: put `<name>_api_url = ...` override lines under `[DEFAULT]` in this node's worker confs (`/etc/cloudx/compute-worker/compute-worker.conf`, and the same for `metadata-proxy`, `telemetry-worker`, `lb-worker`, `vpn-worker`, `bgp-worker`). An override beats the catalog but has to be repeated per worker and per node.

4. Start the worker daemons (`compute-worker`):
   ```bash
   sudo systemctl enable --now cloudx-compute-worker
   ```

---

## 📊 4. Multi-Node Verification & Testing

### **1. Check the worker heartbeat in the Placement API**
Run this on VM 1 (control plane) or from the host laptop:

```bash
cloudx placement provider list
```

**Expected result**:
```text
+-------------------+--------------------------------------+
| Name              | UUID                                 |
+-------------------+--------------------------------------+
| cloudx-compute-01 | a1b2c3d4-e5f6-7890-abcd-1234567890ab |
+-------------------+--------------------------------------+
```

---

### **2. Check the OVN Geneve tunnel status**
Run this on VM 1 or VM 2:

```bash
sudo ovn-sbctl show
```

**Expected result**:
There is a `Chassis` entry for `cloudx-compute-01` with `encap ip "192.168.10.11"`.

---

### **3. Automated E2E test (`scripts/test-multinode-vm.py`)**
Run the multi-node test script from VM 1 or VM 2:

```bash
python3 scripts/test-multinode-vm.py --controller 192.168.10.10 --worker-node cloudx-compute-01
```

This script verifies the health of all control plane REST APIs, node registration in the Placement API, and the OVN chassis tunnel connectivity.
