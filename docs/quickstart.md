# Quickstart: CloudX on one machine

This guide installs the whole CloudX stack (control plane and compute) on a single Ubuntu 24.04 machine from the `.deb` packages, then boots a VM and reaches it over a floating IP. Plan for about 30 minutes.

## What you need

| | |
|---|---|
| OS | Ubuntu 24.04 amd64 (fresh install is best) |
| Resources | 4 vCPU, 8 GB RAM (the control plane alone uses about 4 GB), 40 GB system disk or more. The first start of all services takes a minute or two |
| Disks | **A second, empty disk of 30 GB or more** for volumes (`STORAGE_DISK` below). Every VM gets a root volume, so a VM cannot be created without a storage backend |
| Virtualization | `/dev/kvm` must exist; the compute worker refuses to start without it. On a VM, enable nested virtualization and use the host CPU type (changing the CPU type needs a full stop and start of the VM, a reboot from inside is not enough) |
| Network | **Two network interfaces.** One with the machine's normal address and internet access (called `MGMT_IP` below). One extra interface for the provider (public) network; CloudX attaches it to the `br-ex` bridge, so do not use the interface you are logged in through |
| Access | A sudo user |

Find your values first and keep them for later steps:

```bash
ip -br addr                                   # pick the management address and the spare interface
export MGMT_IP=192.168.10.10                  # your management address
export PROVIDER_IF=enp0s8                     # the spare interface (no IP address needed)
export STORAGE_DISK=/dev/sdb                  # the empty disk for volumes (lsblk shows it without partitions)
test -e /dev/kvm && echo "kvm ok"
lsblk "$STORAGE_DISK"
```

## 1. Get the packages

Download the 47 `.deb` files of a release (about 60 MB) into one directory, for example `~/cloudx-debs`. `SHA256SUMS` lists the file names, so it is also the index to download from; no GitHub account or CLI is needed:

```bash
TAG=v1.0.1                                      # the release you want
BASE=https://github.com/sandyxd18/cloudx/releases/download/$TAG
mkdir -p ~/cloudx-debs && cd ~/cloudx-debs
curl -fsSLO $BASE/SHA256SUMS
awk '$2 ~ /\.deb$/ {print $2}' SHA256SUMS | xargs -P 4 -I{} curl -fsSLO $BASE/{}
sha256sum -c SHA256SUMS --ignore-missing        # every .deb must say OK
```

The file names of a pre-release read `1.0.0.rc1` where the package version is `1.0.0~rc1-1`: GitHub does not keep a `~` in asset names. `apt` takes the version from inside the package, so this only matters if you type a file name by hand. More on releases and file names is in `deb-install.md` section 3.

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

This is the controller role (`cloudx-controller`, its services, `cloudx-db` and `cloudx-cli`) with nginx in front of the APIs. The storage role is set up in step 6.

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
  --provider-interface "$PROVIDER_IF"
sudo systemctl restart cloudx-compute-worker cloudx-metadata-proxy cloudx-telemetry-worker \
     cloudx-lb-worker cloudx-vpn-worker cloudx-bgp-worker
```

Leave `--node-name` out on a single machine. The compute worker starts as soon as its package is installed and registers as `cloudx-compute-01`; the script uses the same name. A different name would leave a dead hypervisor behind, because the CLI cannot delete hypervisors.

`--controller-ip 127.0.0.1` works because everything runs on this machine. The script also removes the OVN chassis that `ovn-central`'s own `ovn-controller` registered under a random name (OVN refuses a second chassis with the same encap IP), and writes the `physnet1:br-ex` mapping the provider network needs. If OVN does not connect afterwards (`sudo ovn-sbctl show` shows no chassis), see `troubleshooting.md`, "OVN chassis does not register".

Check that the node registered and that libvirt is usable from CloudX's Python environment:

```bash
cloudx hypervisor list                         # cloudx-compute-01 should be listed
/opt/cloudx/venv/bin/python -c "import libvirt; print('libvirt ok')"
sudo ovs-vsctl show | grep -E "br-ex|br-int"
```

## 6. Set up block storage

Every VM gets a root volume from the block-store service, so volumes need a backend before the first VM. This guide uses LVM on the empty disk `STORAGE_DISK`; volumes are exported over iSCSI, also on a single machine (the setup installs `targetcli` and `open-iscsi`). The script refuses a disk that carries a filesystem or other signature unless you add `--wipe`, which erases it.

```bash
cd ~/cloudx-debs
STORAGE_ONLY='common|storage|block-store-worker'
sudo apt install --no-install-recommends $(ls ./cloudx-*.deb | grep -E "cloudx-(${STORAGE_ONLY})_")
sudo cloudx-setup-storage --host cloudx-compute-01 --portal-ip "$MGMT_IP" --device "$STORAGE_DISK" --local-api
sudo systemctl restart cloudx-block-store-worker cloudx-block-store-api
```

`--local-api` lists this machine in `block_store_lvm_hosts` of the `block-store-api` that runs here. The worker creates the volume group `vg-cloudx` on its first start. Check it, then create the volume type the API asks for:

```bash
sudo vgs vg-cloudx
cloudx volume-type create gp2 --backend lvm --disk-type sata_ssd --max-iops 3000 --max-throughput 125
cloudx volume-type list
```

## 7. Create the public (provider) network

Use the address range of the network behind `PROVIDER_IF`: its CIDR, the gateway that routes it to the outside, and a free range for floating IPs. The values below are an example; replace all four.

Do not use `172.17.0.0/16`. Docker's default bridge (`docker0`) takes `172.17.0.1/16` on any host where Docker is installed, which breaks routing to that range from the host (see `troubleshooting.md`, "Provider network overlaps the Docker bridge").

```bash
export PUBLIC_CIDR=192.168.100.0/24 PUBLIC_GW=192.168.100.1
export POOL_START=192.168.100.100 POOL_END=192.168.100.200

cloudx network create public-net --external
cloudx subnet create public-subnet \
  --network public-net \
  --cidr "$PUBLIC_CIDR" \
  --gateway "$PUBLIC_GW" \
  --allocation-start "$POOL_START" \
  --allocation-end "$POOL_END"
```

## 8. Boot your first VM

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

## 9. Reach the VM

```bash
cloudx floating-ip create public-net           # note the address it prints
cloudx floating-ip associate <floating-ip> --server demo-vm --router demo-router
ping -c 3 <floating-ip>
ssh -i demo-key.pem cirros@<floating-ip>       # CirrOS user "cirros"
```

The floating IP is reachable from machines on the network behind `PROVIDER_IF`. From the host itself, give `br-ex` a free address of that network first and send the traffic out of it explicitly, because another interface may already hold a route for the range:

```bash
sudo ip addr add 192.168.100.2/24 dev br-ex && sudo ip link set br-ex up    # an unused address of your PUBLIC_CIDR
ping -I br-ex -c 3 <floating-ip>
sudo ip route add <floating-ip>/32 dev br-ex src 192.168.100.2              # only if ssh says "No route to host"
```

Remove the address and the route again when you are done (`ip addr del`, `ip route del`).

## 10. The web dashboard

Open `http://<MGMT_IP>:8080/` and log in as `admin` with the password from step 4.

## Known limitations

- **Metadata service.** A VM cannot reach `http://169.254.169.254` unless the host it runs on has the metadata redirect (`scripts/setup-metadata-route.sh`, see `metadata-proxy.md`), and that is a single-host setup. SSH keys and user-data are delivered through the config drive instead, which is why the CirrOS VM above boots and accepts your key without the metadata service. Images that only read the network metadata endpoint get no data on compute nodes.
- **Internet access from VMs.** A VM with a floating IP leaves the router with that address, and your network must route or NAT the provider range upstream. If pings to the outside get no reply, see `troubleshooting.md`, "VM has no outbound access".
- **Block storage** here is one LVM disk on the same machine. For several storage nodes see `deb-install.md` (4.3); for Ceph see 4.2, step 5.

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
- `troubleshooting.md`: common failures, including floating IPs, metadata and provider-network pitfalls
- `ha.md`, `dns.md`, `k8s-engine.md`: optional services that need extra host preparation
