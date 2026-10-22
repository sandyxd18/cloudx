# Quickstart: CloudX on one machine

This guide installs the whole CloudX stack (control plane and compute) on a single Ubuntu 24.04 machine from the `.deb` packages, then boots a VM and reaches it over a floating IP. Plan for about 30 minutes.

> **Status.** The commands below follow the steps that are verified on the six-node lab (`deb-install.md`) and the CLI flows used by `scripts/test-*.sh`. The complete path on one machine that never had CloudX on it has not been run end to end yet. Steps marked **(unverified)** are the ones to watch; if one fails, please open an issue with the output.

## What you need

| | |
|---|---|
| OS | Ubuntu 24.04 amd64 (fresh install is best) |
| Resources | 4 vCPU, 8 GB RAM, 40 GB disk or more |
| Virtualization | `/dev/kvm` must exist. On a VM, enable nested virtualization |
| Network | **Two network interfaces.** One with the machine's normal address and internet access (called `MGMT_IP` below). One extra interface for the provider (public) network; CloudX attaches it to the `br-ex` bridge, so do not use the interface you are logged in through |
| Access | A sudo user |

Find your values first and keep them for later steps:

```bash
ip -br addr                                   # pick the management address and the spare interface
export MGMT_IP=192.168.10.10                  # your management address
export PROVIDER_IF=enp0s8                     # the spare interface (no IP address needed)
test -e /dev/kvm && echo "kvm ok"
```

## 1. Get the packages

Download the `.deb` files of the release you want and put them in one directory, for example `~/cloudx-debs`:

```bash
mkdir -p ~/cloudx-debs && cd ~/cloudx-debs
# Download every cloudx-*.deb and SHA256SUMS from the GitHub release page,
# https://github.com/sandyxd18/cloudx/releases, then:
sha256sum -c SHA256SUMS --ignore-missing
```

If you build from a checkout instead, `make deb` writes the packages to `dist/deb/`. Building needs `dpkg-dev`, `fakeroot`, Node.js and network access (see `deb-install.md` section 2).

`apt` installs local files and resolves the dependencies between the files you name together, so no repository is needed.

## 2. Install the services CloudX depends on

CloudX uses PostgreSQL, RabbitMQ, memcached and OVN. The packages do not configure them, so do it once:

```bash
sudo apt update
sudo apt install -y postgresql rabbitmq-server memcached ovn-central openvswitch-switch
```

PostgreSQL: one user and the databases CloudX uses. The password below (`cloudx`) is the default in `/etc/cloudx/cloudx.conf`; change both together if you want another.

```bash
sudo -u postgres psql <<'EOF'
CREATE USER cloudx WITH PASSWORD 'cloudx' SUPERUSER;
CREATE DATABASE cloudx OWNER cloudx;
CREATE DATABASE cloudx_orchestration OWNER cloudx;
CREATE DATABASE cloudx_events OWNER cloudx;
CREATE DATABASE cloudx_billing OWNER cloudx;
CREATE DATABASE cloudx_k8s_engine OWNER cloudx;
CREATE DATABASE cloudx_key_manager OWNER cloudx;
EOF
```

RabbitMQ: the `cloudx` user.

```bash
sudo rabbitmqctl add_user cloudx cloudx
sudo rabbitmqctl set_user_tags cloudx administrator
sudo rabbitmqctl set_permissions -p / cloudx ".*" ".*" ".*"
```

OVN central: listen for the OVN controller on TCP.

```bash
sudo ovn-nbctl set-connection ptcp:6641:0.0.0.0
sudo ovn-sbctl set-connection ptcp:6642:0.0.0.0
```

Check that all four are running: `systemctl is-active postgresql rabbitmq-server memcached ovn-central`.

## 3. Install the control plane

Install the controller packages from the directory you downloaded them to. The `grep` keeps out the packages that belong to compute nodes and the ones that need host preparation first (`dns-worker`, `file-store-worker`, `container-worker`, and the storage role; see `deb-install.md` sections 4.3 and 8):

```bash
cd ~/cloudx-debs
NODE_ONLY='compute|storage|bgp-worker|compute-worker|ha-watchdog|lb-worker|metadata-proxy|telemetry-worker|vpn-worker|block-store-worker|dns-worker|file-store-worker|container-worker'
sudo apt install --no-install-recommends $(ls ./cloudx-*.deb | grep -Ev "cloudx-(${NODE_ONLY})_")
```

This is the controller role (`cloudx-controller`, its services, `cloudx-db` and `cloudx-cli`) with nginx in front of the APIs. Block storage (volumes) is not part of this quickstart because it needs an LVM or Ceph backend; see `storage-backend.md`.

The first install writes `/etc/cloudx/cloudx.conf` with generated secrets. Do not replace it. The services cannot start yet because the database has no tables; they retry every 5 seconds, which is expected.

Create the tables, then restart:

```bash
sudo cloudx-manage db sync
sudo systemctl restart 'cloudx-*'
```

Check:

```bash
curl -s http://127.0.0.1:8005/health          # auth-api answers
systemctl list-units --failed 'cloudx-*'      # should list nothing after a minute
```

## 4. Log in as admin

The `admin` password is generated at install time:

```bash
sudo grep auth_admin_password /etc/cloudx/cloudx.conf
cloudx auth login --username admin --password '<password from above>' --auth-url http://127.0.0.1:8005
cloudx hypervisor list                         # empty until step 5
```

## 5. Prepare the machine as a compute node

`cloudx-setup-compute` installs libvirt and QEMU, configures Open vSwitch and OVN, creates the `br-ex` bridge with your provider interface, and writes the worker configs. It is a separate command because it needs your addresses.

First install the compute packages from the same directory:

```bash
cd ~/cloudx-debs
COMPUTE_ONLY='common|compute|bgp-worker|compute-worker|ha-watchdog|lb-worker|metadata-proxy|telemetry-worker|vpn-worker'
sudo apt install --no-install-recommends $(ls ./cloudx-*.deb | grep -E "cloudx-(${COMPUTE_ONLY})_")
```

On a single machine, point the controller address at the machine itself:

```bash
sudo cloudx-setup-compute \
  --controller-ip 127.0.0.1 \
  --node-ip "$MGMT_IP" \
  --node-name cloudx-aio \
  --provider-interface "$PROVIDER_IF"
sudo systemctl restart cloudx-compute-worker cloudx-metadata-proxy cloudx-telemetry-worker \
     cloudx-lb-worker cloudx-vpn-worker cloudx-bgp-worker
```

> **(unverified)** `--controller-ip 127.0.0.1` on the same machine. The lab uses real controller addresses on separate machines. If OVN does not connect (`sudo ovs-vsctl get open_vswitch . external_ids:ovn-remote` and `sudo ovn-sbctl show` show no chassis), repeat with `--controller-ip "$MGMT_IP"`, and in `/etc/postgresql/16/main/postgresql.conf` set `listen_addresses = '*'` with a matching `pg_hba.conf` line for your subnet.

Check that the node registered and that libvirt is usable from CloudX's Python environment:

```bash
cloudx hypervisor list                         # cloudx-aio should be listed
/opt/cloudx/venv/bin/python -c "import libvirt; print('libvirt ok')"
sudo ovs-vsctl show | grep -E "br-ex|br-int"
```

## 6. Create the public (provider) network

Use the address range of the network behind `PROVIDER_IF`. The example below is the range used on the lab; change all four values.

```bash
cloudx network create public-net --external
cloudx subnet create public-subnet \
  --network public-net \
  --cidr 172.17.0.0/16 \
  --gateway 172.17.0.1 \
  --allocation-start 172.17.0.100 \
  --allocation-end 172.17.0.200
```

## 7. Boot your first VM

Download a small test image (CirrOS) and create a private network with a router to the public network:

```bash
curl -LO https://download.cirros-cloud.net/0.6.2/cirros-0.6.2-x86_64-disk.img

cloudx network create demo-net
cloudx subnet create demo-subnet --network demo-net --cidr 10.10.0.0/24 --gateway 10.10.0.1 --dhcp
cloudx router create demo-router
cloudx router add-interface demo-router --subnet demo-subnet
cloudx router set-gateway demo-router public-net

cloudx image upload cirros --file cirros-0.6.2-x86_64-disk.img --disk-format qcow2
cloudx keypair create demo-key                 # writes demo-key.pem
chmod 600 demo-key.pem
```

Allow SSH and ping, then boot. The built-in flavors are `tiny` (1 vCPU, 512 MB), `small`, and larger ones (`cloudx flavor list`).

```bash
cloudx security-group create demo-sg -d "ssh and ping"
cloudx security-group rule add demo-sg --direction ingress --protocol tcp --port 22 --remote-ip 0.0.0.0/0
cloudx security-group rule add demo-sg --direction ingress --protocol icmp --remote-ip 0.0.0.0/0

cloudx server create demo-vm --image cirros --flavor tiny --network demo-net \
  --key-name demo-key --security-group demo-sg
cloudx server show demo-vm                     # repeat until status is ACTIVE
```

If the status becomes `ERROR`, `cloudx server show demo-vm` has the reason and `journalctl -u cloudx-compute-worker -n 50` has the details.

## 8. Reach the VM

```bash
cloudx floating-ip create public-net           # note the address it prints
cloudx floating-ip associate <floating-ip> --server demo-vm --router demo-router
ping -c 3 <floating-ip>
ssh -i demo-key.pem cirros@<floating-ip>       # CirrOS user "cirros"
```

The floating IP is reachable from machines on the network behind `PROVIDER_IF`. From the host itself, add an address of that network to `br-ex` first, for example `sudo ip addr add 172.17.0.2/16 dev br-ex && sudo ip link set br-ex up`.

## 9. The web dashboard

Open `http://<MGMT_IP>:8080/` and log in as `admin` with the password from step 4.

## Clean up

```bash
cloudx server delete demo-vm
cloudx floating-ip delete <floating-ip>
cloudx router remove-interface demo-router demo-subnet
cloudx router delete demo-router
cloudx subnet delete demo-subnet
cloudx network delete demo-net
```

## Where to go next

- `deb-install.md`: multiple nodes, upgrades, rollback, service placement
- `user-guide-cli.md`: the full CLI
- `troubleshooting.md`: common failures
- `ha.md`, `dns.md`, `k8s-engine.md`: optional services that need extra host preparation
