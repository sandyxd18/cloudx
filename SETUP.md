# CloudX Setup & Deployment Guide (Native systemd)

This comprehensive guide describes how to install, configure, and operate **CloudX** natively with **systemd services**, both for the fully validated **3-node production** architecture (Controller, Compute, Storage) and for quick **single-node all-in-one** testing.

---

## 🏗️ 1. Multi-Node Production Architecture (3-Node Topology)

The CloudX production architecture separates the control plane, the virtualisation compute (hypervisor), and the distributed data storage (shared storage):

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
                     │  (8 GB RAM, 4 vCPU) │ │ (8 GB RAM, 4C) │ │ (8 GB RAM, 4C) │
                     └──────────┬──────────┘ └───────┬────────┘ └────────┬───────┘
                                │                    │                   │
                                │                    │                   │
 [Services]                     ▼                    ▼                   ▼
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

### Node Resource Allocation

| Node role | Hostname | Management IP | RAM | vCPU | OS disk | Data disk | Main services |
|---|---|---|---|---|---|---|---|
| **Controller** | `cloudx-control-plane` | `192.168.10.10` | 8 GB | 4 | 30 GB | - | PostgreSQL, RabbitMQ, OVN-Central, REST APIs, CloudX Web Dashboard |
| **Compute** | `cloudx-compute-01` | `192.168.10.11` | 8 GB | 4 | 30 GB | 30 GB (`/dev/sdb`, free) | KVM/QEMU, Libvirt, OVS/OVN-Host, `compute-worker`, `metadata-proxy`, NFS client |
| **Storage** | `cloudx-storage-01` | `192.168.10.12` | 8 GB | 4 | 30 GB | 30 GB (`/dev/sdb`) | NFS kernel server, `block-store-worker`, MinIO S3 object store |

> The sizes above are the ones measured on the current lab (4 vCPU / 8 GB per node, nested virtualisation enabled).
> The platform itself runs with less (about 4 GB / 2 vCPU for the compute and storage nodes), but the control plane
> and a few guest VMs are more comfortable with these values. For the LVM-on-two-nodes scenario see
> `docs/two-node-lvm-lab.md`.

---

## 🛠️ 2. Installation Steps for Node 1: Controller Node (`192.168.10.10`)

### 2.1 Dependency Packages & Database
Run on the Controller node:
```bash
sudo apt update && sudo apt install -y \
  python3-venv python3-pip python3-dev build-essential \
  postgresql postgresql-contrib memcached rabbitmq-server \
  openvswitch-switch ovn-central ovn-common \
  curl git jq net-tools nfs-common
```

### 2.2 PostgreSQL Remote Listen Configuration
Allow the Compute and Storage nodes to reach PostgreSQL:
```bash
# 1. Edit /etc/postgresql/14/main/postgresql.conf
sudo sed -i "s/#listen_addresses = 'localhost'/listen_addresses = '*'/g" /etc/postgresql/14/main/postgresql.conf

# 2. Allow the management subnet in /etc/postgresql/14/main/pg_hba.conf
echo "host    all             all             192.168.10.0/24         md5" | sudo tee -a /etc/postgresql/14/main/pg_hba.conf

sudo systemctl restart postgresql
```

Create the CloudX user and databases (credentials and database names can be changed; adjust the `DATABASE_URL` values in the service units if you change the credentials):
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

### 2.3 RabbitMQ Broker Configuration
Create the user and permissions in RabbitMQ:
```bash
sudo systemctl enable --now rabbitmq-server
sudo rabbitmqctl add_user cloudx cloudx
sudo rabbitmqctl set_user_tags cloudx administrator
sudo rabbitmqctl set_permissions -p / cloudx ".*" ".*" ".*"
```

### 2.4 OVN Central Listener Configuration
Allow `ovn-controller` on the compute node to connect to the Southbound DB:
```bash
# Listen on TCP port 6641 (Northbound) and 6642 (Southbound)
sudo ovn-nbctl set-connection ptcp:6641:0.0.0.0
sudo ovn-sbctl set-connection ptcp:6642:0.0.0.0
```

### 2.5 Installing the CloudX Release on the Controller

The CloudX release package can be downloaded directly from **GitHub Releases** (for servers with internet access) or through the Controller's HTTP gateway (for isolated lab environments).

```bash
# Replace v0.7.0 with the release version you want
curl -sSL -L https://github.com/sandyxd18/cloudx/releases/download/v0.7.0/cloudx-0.7.0.tar.gz -o cloudx-latest.tar.gz
```

#### Extraction and installation:
```bash
# 1. Create the 'cloudx' system user & group and the working directories
sudo groupadd -r cloudx 2>/dev/null || true
sudo useradd -r -g cloudx -d /opt/cloudx -s /sbin/nologin -c 'CloudX Service User' cloudx 2>/dev/null || true
sudo mkdir -p /opt/cloudx /var/lib/cloudx/images /var/lib/cloudx/volumes /var/lib/cloudx/instances /etc/cloudx

# 2. Extract the release package
sudo tar -xzf cloudx-latest.tar.gz -C /opt/cloudx/

# 3. Create the Python venv & install the dependencies
sudo python3 -m venv /opt/cloudx/venv
sudo /opt/cloudx/venv/bin/pip install --upgrade pip
sudo /opt/cloudx/venv/bin/pip install -r /opt/cloudx/requirements.txt
sudo /opt/cloudx/venv/bin/pip install -e /opt/cloudx/cli/ --no-deps
# The shared library 'shared' + the cloudx-db-migrate command
sudo /opt/cloudx/venv/bin/pip install /opt/cloudx/services --no-deps
sudo ln -sfn /opt/cloudx/venv/bin/cloudx /usr/local/bin/cloudx

# 4. Install the systemd service units & set directory ownership
sudo cp /opt/cloudx/etc/systemd/*.service /etc/systemd/system/
sudo chown -R cloudx:cloudx /opt/cloudx /var/lib/cloudx /etc/cloudx
sudo systemctl daemon-reload

# 5. (Controller) Create the shared secrets. Copy the SAME file to every other node that runs an API service
sudo install -m 640 -o root -g cloudx /dev/null /etc/cloudx/cloudx.conf
{ echo "AUTH_SECRET_KEY=$(openssl rand -hex 32)"; echo "AUTH_ADMIN_PASSWORD=$(openssl rand -base64 18 | tr -d '/+=')"; echo "KEY_MANAGER_MASTER_KEY=$(openssl rand -hex 32)"; } | sudo tee /etc/cloudx/cloudx.conf >/dev/null

# 6. (Controller) Create the database schema of all services. Safe to repeat; mandatory before the services start
sudo CLOUDX_SERVICES_DIR=/opt/cloudx/services /opt/cloudx/venv/bin/cloudx-db-migrate
```

Enable the control plane services on the Controller:
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
  cloudx-k8s-engine-worker \
  cloudx-telemetry-api \
  cloudx-billing-api \
  cloudx-event-api \
  cloudx-object-store-api \
  cloudx-load-balancer-api \
  cloudx-dashboard
```

### 2.6 Setting the Admin User Password
The administrator account (`admin`) is created automatically by `cloudx-auth-api` the first time it runs. Its password has **no default value**: it is read from `AUTH_ADMIN_PASSWORD` in `/etc/cloudx/cloudx.conf`, which `scripts/install-systemd.sh` (or step 5 above) generates randomly.

#### Option A: View or set the initial admin password
```bash
sudo grep AUTH_ADMIN_PASSWORD /etc/cloudx/cloudx.conf     # show the current password
sudoedit /etc/cloudx/cloudx.conf                          # (optional) change the value, then:
sudo systemctl restart cloudx-auth-api
```
`auth-api` hashes that password (bcrypt) and sets it as the `admin` account's password **on every restart**. A password changed through the CLI/Dashboard (Option B) is therefore overwritten on restart unless the value in `/etc/cloudx/cloudx.conf` is changed too.

#### Option B: Change the admin password at any time after the system is running (runtime)
If the system is already installed and running, you can update the admin user's password at any time with one of these methods:

1. **Through the CloudX CLI**:
   ```bash
   cloudx auth user set admin --password "YourNewPassword123!"
   ```
2. **Through the CloudX Web Dashboard**:
   Open the dashboard in a browser at `http://192.168.10.10:8080/`, log in, open the **Admin ➔ Users** menu, click the **admin** user, choose **Edit User**, and enter the new password.
3. **Through the REST API directly**:
   ```bash
   curl -X PUT http://127.0.0.1:8005/users/<ADMIN_USER_ID> \
     -H "X-Auth-Token: <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{"password": "YourNewPassword123!"}'
   ```

### 2.7 Defining the Floating IP Pool and Initialising the Public / Provider Network

The networking engine (OVN & OVS) that is already active at the infrastructure level **does not automatically create Logical Network objects** in the CloudX database. On a fresh installation you need to initialise the first Logical Network and public Subnet so that Floating IP allocation can work.

#### Standard steps to initialise the provider network & floating IPs (via the CloudX CLI)

Run the following on the Controller node (or any node with a configured `cloudx` CLI):

```bash
# 1. Log in as Admin
cloudx auth login --username admin --password "<AUTH_ADMIN_PASSWORD from /etc/cloudx/cloudx.conf>" --auth-url http://127.0.0.1:8005

# 2. Create the external provider network (connected to br-ex / physnet1 on the provider NIC)
cloudx network create public-net --external

# 3. Create the public subnet with the floating IP pool range
# (Adjust the CIDR, gateway, and allocation pool to your provider subnet, e.g. 172.17.0.0/16 or 172.24.4.0/24)
cloudx subnet create public-subnet \
  --network public-net \
  --cidr 172.17.0.0/16 \
  --gateway 172.17.0.1 \
  --allocation-start 172.17.0.100 \
  --allocation-end 172.17.0.200

# 4. Verify that the network & subnet objects are registered
cloudx network list
cloudx subnet list

# 5. Test the first floating IP allocation (the IP is taken automatically from the pool 172.17.0.100 - 172.17.0.200)
cloudx floating-ip create public-net
```

#### Advanced option: default configuration through environment variables (systemd override)
If you want to set the provider network pool defaults at the service level so they are used automatically during auto-provisioning:
1. Create a systemd override file for `cloudx-networking-api`:
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
2. Reload systemd and restart the service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl restart cloudx-networking-api
   ```

---

## 💾 3. Installation Steps for Node 2: Storage Node (`192.168.10.12`)

### 3.1 Dedicated Disk Format & Mount Point
The storage node uses the dedicated disk `/dev/sdb` (30 GB), exported over high-performance NFS:
```bash
# 1. Format the data disk with ext4
sudo mkfs.ext4 -F /dev/sdb

# 2. Create the mount directory and add it to /etc/fstab
sudo mkdir -p /srv/cloudx
UUID=$(sudo blkid -s UUID -o value /dev/sdb)
echo "UUID=$UUID /srv/cloudx ext4 defaults 0 2" | sudo tee -a /etc/fstab
sudo mount -a

# 3. Create the volume subfolders and permissions
sudo mkdir -p /srv/cloudx/volumes /srv/cloudx/minio-data
sudo chmod 777 /srv/cloudx/volumes
sudo mkdir -p /var/lib/cloudx
sudo ln -sfn /srv/cloudx/volumes /var/lib/cloudx/volumes
```

### 3.2 NFS Server Configuration
Install the NFS kernel server and share the volume directory with the management subnet:
```bash
sudo apt update && sudo apt install -y nfs-kernel-server qemu-utils python3-venv python3-pip

# Export configuration
echo "/srv/cloudx/volumes 192.168.10.0/24(rw,sync,no_subtree_check,no_root_squash)" | sudo tee /etc/exports
sudo exportfs -ra
sudo systemctl restart nfs-kernel-server
sudo systemctl enable nfs-kernel-server
```

### 3.3 Installing the CloudX Block Store Worker on the Storage Node

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

Configure the service unit `/etc/systemd/system/cloudx-block-store-worker.service`:
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

Start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now cloudx-block-store-worker.service
```

---

## ⚡ 4. Installation Steps for Node 3: Compute Node (`192.168.10.11`)

### 4.1 Virtualisation & OVS/OVN Dependencies
Run on the Compute node:
```bash
sudo apt update && sudo apt install -y \
  qemu-kvm libvirt-daemon-system libvirt-clients \
  openvswitch-switch ovn-host nfs-common qemu-utils qemu-guest-agent \
  genisoimage cloud-image-utils libvirt-dev pkg-config build-essential \
  python3-venv python3-pip python3-dev
# After the venv exists (section 4.4): the worker drives libvirt through this binding. Without it the worker
# starts fine but cannot create VMs ("libvirt = None" fallback).
sudo /opt/cloudx/venv/bin/pip install libvirt-python
```

### 4.2 Mounting the Shared NFS Storage
Mount the volume directory from the Storage Node (`192.168.10.12`):
```bash
sudo mkdir -p /var/lib/cloudx/volumes
echo "192.168.10.12:/srv/cloudx/volumes /var/lib/cloudx/volumes nfs defaults,_netdev 0 0" | sudo tee -a /etc/fstab
sudo mount /var/lib/cloudx/volumes
```

### 4.3 OVS, OVN Controller & Provider Bridge Configuration
Connect the compute node to the OVN Southbound Database on the Controller:
```bash
# 1. Create the integration and provider bridges
sudo ovs-vsctl --may-exist add-br br-int -- set Bridge br-int fail-mode=secure
sudo ovs-vsctl --may-exist add-br br-ex

# 2. Attach the provider interface (NIC 3, e.g. enp0s9) to br-ex
sudo ip link set enp0s9 up
sudo ip link set br-ex up
sudo ovs-vsctl --may-exist add-port br-ex enp0s9

# 3. Set the OVN Controller configuration
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

### 4.4 Installing & Running the Worker Daemons on the Compute Node

```bash
sudo groupadd -r cloudx 2>/dev/null || true
sudo useradd -r -g cloudx -d /opt/cloudx -s /sbin/nologin -c 'CloudX Service User' cloudx 2>/dev/null || true
sudo mkdir -p /opt/cloudx /var/lib/cloudx/instances /etc/cloudx

curl -sSL -L https://github.com/sandyxd18/cloudx/releases/download/v0.7.0/cloudx-0.7.0.tar.gz -o /tmp/cloudx-latest.tar.gz

sudo tar -xzf /tmp/cloudx-latest.tar.gz -C /opt/cloudx/

sudo python3 -m venv /opt/cloudx/venv
sudo /opt/cloudx/venv/bin/pip install --upgrade pip
sudo /opt/cloudx/venv/bin/pip install -r /opt/cloudx/requirements.txt

# Install the service units & set directory ownership
sudo cp /opt/cloudx/etc/systemd/*.service /etc/systemd/system/
sudo chown -R cloudx:cloudx /opt/cloudx /var/lib/cloudx /etc/cloudx
```

Configure the service unit `/etc/systemd/system/cloudx-compute-worker.service`:
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

Start the worker daemons:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now cloudx-compute-worker.service cloudx-metadata-proxy.service
```

---

## ✅ 5. End-to-End Verification & Testing of the 3-Node Cluster

Run a full test directly from the CloudX CLI on the Controller node:

### 5.1 Check Hypervisor & Compute Status
```bash
cloudx hypervisor list
```
*Expected output:*
```
+-------------------+-------------------+----------+---------------+
| host              | name              | status   |   running_vms |
+===================+===================+==========+===============+
| cloudx-compute-01 | cloudx-compute-01 | active   |             0 |
+-------------------+-------------------+----------+---------------+
```

### 5.2 Creating a Volume on the Storage Node
```bash
cloudx volume create vol-prod-data --size 2 --type nfs-storage
cloudx volume list
```
*Physical verification on the Storage Node (`192.168.10.12`):*
```bash
ls -lh /srv/cloudx/volumes/
```

### 5.3 Launching a Virtual Machine on the Compute Node
```bash
cloudx server create vm-app-01 --image cirros-0.6.2-x86_64 --flavor tiny --network private-net
cloudx server list
```
*Verify the libvirt domain on the Compute Node (`192.168.10.11`):*
```bash
sudo virsh list
```

### 5.4 Cross-Node Volume Hotplug Attach
Attach a storage volume (Storage Node) to a running VM (Compute Node):
```bash
cloudx volume attach vol-prod-data --server vm-app-01
```
*Check the disks on the hypervisor:*
```bash
sudo virsh domblklist cloudx-vm-app-01-<ID>
# Shows sda (qcow2 root disk) and sdb (qcow2 data disk from NFS)
```

### 5.5 Test Floating IP Association & Network Access
Associate a public Floating IP and test connectivity from outside:
```bash
cloudx floating-ip associate 172.24.4.100 --server vm-app-01 --router public-router
ping -c 3 172.24.4.100
ssh cirros@172.24.4.100
```

---

## 💻 6. Complete CloudX CLI Usage Guide

The CloudX CLI (`cloudx`) is a unified command-line interface for managing the whole lifecycle of your private cloud infrastructure from the terminal.

### 6.1 CLI Installation & Setup

#### On the Controller Node
The CLI is already installed automatically at `/usr/local/bin/cloudx` (linked to `/opt/cloudx/venv/bin/cloudx`). You can use it right away.

#### On a Remote Machine / User Laptop
If you want to manage the CloudX cluster from your local computer or workstation:
```bash
# 1. Download the CloudX repository
git clone <CLOUDX_REPO_URL> cloudx
cd cloudx

# 2. Install the CLI package into a local virtualenv
python3 -m venv .venv
source .venv/bin/activate
pip install -e cli/
```

---

### 6.2 CLI Authentication Methods (Choose One)

The CloudX CLI supports three flexible authentication methods:

#### Method 1: Interactive Login (Highly Recommended)
This command asks for the admin password you have set, verifies the account against the Auth API, and automatically creates a permanent Access Key credential in `~/.cloudx/credentials`:
```bash
cloudx auth login --username admin --auth-url http://192.168.10.10:8005
# Enter your admin password when prompted
```
*Successful output:*
```
☁  cloudx auth login

Authenticating... ✓
✓ Successfully logged in as 'admin' in project 'admin'
✓ Credentials saved to ~/.cloudx/credentials
✓ Configuration saved to ~/.cloudx/config
```

#### Method 2: Environment Variables (Good for Scripting / CI/CD)
You can export the following environment variables in the terminal or a shell script without entering the password manually:
```bash
export CLOUDX_AUTH_URL="http://192.168.10.10:8005"
export CLOUDX_USERNAME="admin"
export CLOUDX_PASSWORD="<Your_Admin_Password>"
```
Once exported, every `cloudx` command is authenticated automatically.

#### Method 3: Manual Access Key Configuration
If you have an Access Key ID & Secret Access Key generated from the Web Dashboard:
```bash
cloudx auth configure
# Enter the CloudX Access Key ID
# Enter the CloudX Secret Access Key
# Enter the Default project (default: admin)
# Enter the Output format (default: table)
```

---

### 6.3 Everyday Operational CLI Examples

#### A. Compute Management (Virtual Machines)
```bash
# List flavors (vCPU / RAM sizes)
cloudx flavor list

# List the available OS images
cloudx image list

# Create a new VM
cloudx server create vm-web-01 \
  --image cirros-0.6.2-x86_64 \
  --flavor tiny \
  --network private-net

# List VM instances
cloudx server list

# Show full details of an instance's specification and IPs
cloudx server show vm-web-01

# Control VM state (Reboot, Stop, Start, Pause, Resume)
cloudx server reboot vm-web-01
cloudx server stop vm-web-01
cloudx server start vm-web-01

# Delete a VM
cloudx server delete vm-web-01
```

#### B. Storage Management (Block Storage Volumes)
```bash
# Create a new storage volume (size in GB)
cloudx volume create vol-data-01 --size 5 --type nfs-storage

# List volumes
cloudx volume list

# Attach a volume to a running VM (hotplug)
cloudx volume attach vol-data-01 --server vm-web-01

# Detach a volume from a VM
cloudx volume detach vol-data-01

# Create a volume snapshot
cloudx snapshot create snap-vol-01 --volume vol-data-01
cloudx snapshot list
```

#### C. Network & Public IP Management (Networking)
```bash
# Show virtual networks and subnets
cloudx network list
cloudx subnet list

# Show virtual routers and gateway routing
cloudx router list

# List public Floating IPs
cloudx floating-ip list

# Associate a public Floating IP with a VM
cloudx floating-ip associate 172.24.4.100 --server vm-web-01 --router public-router

# Disassociate a Floating IP
cloudx floating-ip disassociate 172.24.4.100

# Show firewall rules (Security Groups)
cloudx security-group list
```

#### D. Identity & User Management (Auth & Identity)
```bash
# List users and projects
cloudx auth user list
cloudx auth project list

# Create a new project/tenant
cloudx auth project create production --description "Production Workloads"

# Create a new user
cloudx auth user create devuser \
  --email devuser@cloudx.local \
  --password "UserSecretPassword123!" \
  --project production \
  --role member

# Change a user's password
cloudx auth user set devuser --password "NewUserSecretPassword123!"
```

---

## 🌐 7. Accessing the CloudX Web Dashboard

Once all services are active:
* **URL**: `http://192.168.10.10:8080/`
* **Username**: `admin`
* **Password**: the value of `AUTH_ADMIN_PASSWORD` in `/etc/cloudx/cloudx.conf` on the Controller (or the one you set yourself, see 2.6)

The dashboard provides real-time visualisation of the cluster topology, compute node CPU/RAM utilisation, storage node disk quota, and the instance VNC console.
