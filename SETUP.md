# CloudX setup guide

How to install CloudX on Ubuntu 24.04 from the `.deb` packages, on one machine or on several. This page covers the choices and the parts that are the same everywhere: requirements, the services CloudX depends on, the admin account and the first network. The step-by-step install of each role is in [docs/deb-install.md](docs/deb-install.md).

## 1. Choose how to install

| You want | Read |
|---|---|
| Try CloudX on one machine: control plane and compute together, a VM and a floating IP in about 30 minutes | [docs/quickstart.md](docs/quickstart.md) |
| Several nodes: a controller, compute nodes, storage nodes, services spread across hosts | this page (sections 2 to 4), then [docs/deb-install.md](docs/deb-install.md) sections 4 and 5 |
| A larger cluster with Ceph | [docs/deb-install.md](docs/deb-install.md) and [docs/storage-backend.md](docs/storage-backend.md) |
| A node still installed from the release tarball | [docs/tarball-install.md](docs/tarball-install.md) (legacy) and the upgrade runbook in [docs/deb-install.md](docs/deb-install.md) section 7 |

The packages come from the GitHub release page (`cloudx-*.deb` and `SHA256SUMS`); `apt install ./cloudx-*.deb` resolves the dependencies between the files you name. Building them yourself is covered in [docs/deb-install.md](docs/deb-install.md) section 2.

## 2. Requirements

| | |
|---|---|
| OS | Ubuntu 24.04 amd64 with Python 3.12 (the packages carry a Python 3.12 environment; no other distribution is supported) |
| Virtualization | `/dev/kvm` on every compute node; the compute worker refuses to start without it. On a VM, turn on nested virtualization and use the host CPU type |
| Memory | A control plane uses about 4 GB on its own. 8 GB and 4 vCPU per node is comfortable for a small lab; compute and storage nodes run with about 4 GB / 2 vCPU if they host only a few guests |
| Network | A management network that all nodes share. Geneve tunnels between nodes run over the node IP you pass to `cloudx-setup-compute` (the management network in a small lab, a separate one if you have it). Gateway nodes also need a spare interface for the provider (public) network |
| Disks | Every VM gets a root volume, so you need a storage backend: a spare disk for LVM on a storage node (`cloudx-setup-storage`) or a Ceph cluster |

Reference layouts that have been run:

| Layout | Nodes |
|---|---|
| All-in-one | 1 machine: 4 vCPU, 8 GB, a second NIC and a second disk |
| Small cluster | 1 controller, 1 or more compute nodes, 1 storage node (4 vCPU / 8 GB each) |
| Six-node lab | 3 infra nodes (control plane spread across them, OVN gateways, Ceph mon and OSD) and 3 compute nodes |

## 3. Prepare the controller

The packages do not configure the services CloudX depends on. On the controller:

```bash
sudo apt update
sudo apt install -y postgresql rabbitmq-server memcached ovn-central openvswitch-switch
```

### 3.1 PostgreSQL

On a single machine the defaults are enough. With several nodes, PostgreSQL must listen on the management network and accept the other nodes (Ubuntu 24.04 has PostgreSQL 16):

```bash
sudo sed -i "s/^#\?listen_addresses.*/listen_addresses = '*'/" /etc/postgresql/16/main/postgresql.conf
echo "host    all    all    192.168.10.0/24    scram-sha-256" | sudo tee -a /etc/postgresql/16/main/pg_hba.conf
sudo systemctl restart postgresql
```

Create the user and the databases. Use a generated password and keep it for `database_url` in `/etc/cloudx/cloudx.conf` (the package default is the password `cloudx`, which is fine for a throwaway lab only):

```bash
DB_PW=$(openssl rand -hex 16); echo "$DB_PW"
sudo -u postgres psql <<EOF
CREATE USER cloudx WITH PASSWORD '$DB_PW' SUPERUSER;
CREATE DATABASE cloudx OWNER cloudx;
CREATE DATABASE cloudx_orchestration OWNER cloudx;
CREATE DATABASE cloudx_events OWNER cloudx;
CREATE DATABASE cloudx_billing OWNER cloudx;
CREATE DATABASE cloudx_k8s_engine OWNER cloudx;
CREATE DATABASE cloudx_key_manager OWNER cloudx;
EOF
```

### 3.2 RabbitMQ

```bash
MQ_PW=$(openssl rand -hex 16); echo "$MQ_PW"
sudo rabbitmqctl add_user cloudx "$MQ_PW"
sudo rabbitmqctl set_user_tags cloudx administrator
sudo rabbitmqctl set_permissions -p / cloudx ".*" ".*" ".*"
```

### 3.3 OVN central

Let `ovn-controller` on the compute and gateway nodes reach the OVN databases:

```bash
sudo ovn-nbctl set-connection ptcp:6641:0.0.0.0
sudo ovn-sbctl set-connection ptcp:6642:0.0.0.0
```

Check: `systemctl is-active postgresql rabbitmq-server memcached ovn-central`.

[docs/deb-install.md](docs/deb-install.md) section 4.1 continues from here (install the controller packages, set `database_url` and `rabbitmq_url`, `cloudx-manage db sync`). Sections 4.2 and 4.3 cover compute and storage nodes.

## 4. The pieces, by role

| Role | Packages | Host preparation |
|---|---|---|
| Controller | `cloudx-controller` (its services, `cloudx-db`, nginx in front of 20 APIs), `cloudx-cli` | PostgreSQL, RabbitMQ, memcached, OVN central (section 3) |
| Compute | `cloudx-compute` | `cloudx-setup-compute --controller-ip ... --node-ip ... --provider-interface ...` installs libvirt, QEMU and Open vSwitch, sets up OVN and writes the worker configs |
| Storage | `cloudx-storage` | `cloudx-setup-storage --host ... --portal-ip ... --device /dev/sdb` turns a spare disk into the LVM volume group, writes the node's block-store-worker settings and installs `open-iscsi` |

Ports and what listens where: [docs/deb-install.md](docs/deb-install.md) section 1. The full configuration reference is `/usr/share/cloudx/cloudx.conf.sample` on any node with `cloudx-common`.

## 5. The admin account

`cloudx-auth-api` creates the `admin` account the first time it starts. The password has no default: `cloudx-common` generates `auth_admin_password` in `/etc/cloudx/cloudx.conf` when it is first installed.

```bash
sudo grep auth_admin_password /etc/cloudx/cloudx.conf
cloudx auth login --username admin --auth-url http://<controller>:8005
```

`auth-api` hashes the value from `cloudx.conf` and sets it as the admin password on every start. A password changed with the CLI, the dashboard or the API is therefore replaced again at the next restart, unless you change `auth_admin_password` in `cloudx.conf` as well:

```bash
cloudx auth user set admin --password "YourNewPassword123!"      # lasts until auth-api restarts
sudoedit /etc/cloudx/cloudx.conf                                  # make it permanent
sudo systemctl restart cloudx-auth-api
```

Never change `auth_secret_key` after installation: it invalidates every token. Copy the same `cloudx.conf` to every node (see [docs/deb-install.md](docs/deb-install.md) section 4.2).

## 6. The first network

OVN and Open vSwitch are running after the install, but CloudX does not create any logical network for you. Create the external (provider) network and its floating IP pool once, as admin. Use the address range of the network behind the provider interface: its CIDR, the gateway that routes it to the outside, and a free range for floating IPs. The values below are an example.

Do not use `172.17.0.0/16`: Docker's default bridge takes `172.17.0.1/16` on any host with Docker installed (see [docs/troubleshooting.md](docs/troubleshooting.md), "Provider network overlaps the Docker bridge").

```bash
cloudx network create public-net --external
cloudx subnet create public-subnet \
  --network public-net \
  --cidr 192.168.100.0/24 \
  --gateway 192.168.100.1 \
  --allocation-start 192.168.100.100 \
  --allocation-end 192.168.100.200
cloudx network list && cloudx subnet list
cloudx floating-ip create public-net            # allocates an address from the pool
```

To make these the defaults the networking service uses when it creates the external network on its own, set them in `/etc/cloudx/networking-api/networking-api.conf` and restart `cloudx-networking-api`:

```ini
[DEFAULT]
provider_cidr = 192.168.100.0/24
provider_gateway = 192.168.100.1
provider_allocation_start = 192.168.100.100
provider_allocation_end = 192.168.100.200
```

If the gateway address collides with the real LAN gateway, floating IPs answer ARP intermittently; [docs/provider-network.md](docs/provider-network.md) and [docs/troubleshooting.md](docs/troubleshooting.md) explain how to pick the range.

## 7. Check the cluster

From the controller, after logging in:

```bash
cloudx hypervisor list                    # every compute node should be listed
systemctl list-units --failed 'cloudx-*'  # nothing after a minute or two
cloudx server create vm-1 --image <image> --flavor tiny --network <network>
cloudx server list
```

[docs/quickstart.md](docs/quickstart.md) steps 7 to 9 walk through uploading an image, a private network, a router, a security group, a floating IP and SSH. The scripts `scripts/test-real-vm.sh`, `scripts/test-floating-ip.sh` and `scripts/test-security-group.sh` run the same flows against a live cloud.

## 8. The CLI and the dashboard

The CLI is `cloudx` (package `cloudx-cli`, installable on its own on any machine that can reach the auth API). [docs/user-guide-cli.md](docs/user-guide-cli.md) covers authentication (interactive login, environment variables, access keys) and the command groups.

The web dashboard is at `http://<controller>:8080/`. Log in as `admin` with the password from section 5.
