# Tutorial: a three-node cloud (one controller, two compute and storage nodes)

You will build a small cloud on three machines, spread VMs over the two worker nodes, and attach a volume that lives on one node to a VM running on the other. About two hours, most of it waiting for `apt`.

> **Status.** This layout was installed from the `.deb` packages on three bare-metal machines on 2026-10-24, and every command from section 5 on was run there on 2026-10-26 (output shown where it helps). Sections 1 to 4 follow [../deb-install.md](../deb-install.md) step by step; the lab differed in two places that are called out as *lab notes*. Section 6 needs a build that includes the anti-affinity scheduling fix (0.8.6 does not); without it the third group member is not refused.

## What you need

| Node | Role | Runs |
|---|---|---|
| `infra01` | controller | PostgreSQL, RabbitMQ, memcached, OVN central, the APIs, the dashboard |
| `infra02`, `infra03` | compute + storage | VMs (libvirt/KVM), Open vSwitch + OVN, LVM volumes exported over iSCSI |

- Ubuntu 24.04 amd64 on all three, with passwordless `sudo` and working name resolution between them (or use IP addresses throughout).
- `/dev/kvm` on the two worker nodes.
- A network all three share. This tutorial uses one network (the lab's `192.168.15.0/24`) for management, the Geneve tunnels and the provider (public) network, because the machines have a single cable each. With a second NIC per node, pass it as `--provider-interface` below and keep management separate.
- On each worker node, spare disk space for LVM volumes: an empty disk (`/dev/sdb`) or free space in an existing volume group.
- Memory: the lab nodes have 3.7 GB each. The controller then runs a trimmed set of services (below). With 8 GB or more, install the whole `cloudx-controller` role instead.
- The `.deb` files on every node (from the release page, see [../../README.md](../../README.md)), for example in `~/debs`.

Set these once in your shell on each node and adjust them:

```bash
export CTRL_IP=192.168.15.11          # infra01
export NODE_IP=192.168.15.12          # this worker node (infra02: .12, infra03: .13)
export NODE=infra02                   # this worker node's name; must be unique
export PROVIDER_IF=eno1               # the NIC for the public network (see the lab note in section 3)
```

## 1. Prepare the controller

On `infra01`:

```bash
sudo apt update
sudo apt install -y postgresql rabbitmq-server memcached ovn-central openvswitch-switch
```

Create the database user, the databases, the RabbitMQ user and the OVN listeners exactly as in [../../SETUP.md](../../SETUP.md) section 3 (generated passwords, `scram-sha-256` access for your network, listeners on 6641 and 6642). Keep `DB_PW` and `MQ_PW`.

## 2. Install the control plane

```bash
cd ~/debs
sudo apt install --no-install-recommends ./cloudx-common_*.deb ./cloudx-db_*.deb ./cloudx-cli_*.deb \
    ./cloudx-auth-api_*.deb ./cloudx-compute-api_*.deb ./cloudx-conductor_*.deb ./cloudx-networking-api_*.deb \
    ./cloudx-placement-api_*.deb ./cloudx-image-api_*.deb ./cloudx-block-store-api_*.deb \
    ./cloudx-metadata-api_*.deb ./cloudx-dashboard_*.deb
```

That is the smallest set that creates and runs VMs: identity, compute, scheduling, networking, images, block storage, metadata, and the dashboard. Add the other API packages later as you need their features ([../architecture.md](../architecture.md) lists them).

Then, as in [../deb-install.md](../deb-install.md) section 4.1:

1. Put the generated passwords in `/etc/cloudx/cloudx.conf` (`database_url`, `rabbitmq_url`) and set `auth_api_url` to `http://$CTRL_IP:8005`. Per-service config files shipped by a package, such as `/etc/cloudx/conductor/conductor.conf`, carry their own `database_url` with the default password: change it too, and keep the files `640 root:cloudx`.
2. Create the tables: `sudo cloudx-manage db sync`.
3. Tell the catalog where the services are reachable from other nodes. In `/etc/cloudx/auth-api/auth-api.conf`:

   ```ini
   [DEFAULT]
   compute_api_url = http://192.168.15.11:8003
   image_api_url = http://192.168.15.11:8002
   networking_api_url = http://192.168.15.11:8001
   block_store_api_url = http://192.168.15.11:8004
   placement_api_url = http://192.168.15.11:8006
   metadata_api_url = http://192.168.15.11:8775
   ```

   then `sudo cloudx-manage catalog sync --dry-run` (read it), `sudo cloudx-manage catalog sync`, and `sudo systemctl restart 'cloudx-*'`.
4. Log in: `cloudx auth login --username admin --auth-url http://$CTRL_IP:8005`.

## 3. Install the worker nodes

On `infra02` and `infra03`, one at a time. First copy `/etc/cloudx/cloudx.conf` from the controller (same secrets everywhere), as in [../deb-install.md](../deb-install.md) section 4.2 step 1. Then:

```bash
cd ~/debs
sudo apt install --no-install-recommends ./cloudx-common_*.deb ./cloudx-cli_*.deb \
    ./cloudx-compute-worker_*.deb ./cloudx-metadata-proxy_*.deb ./cloudx-block-store-worker_*.deb

sudo cloudx-setup-compute --controller-ip "$CTRL_IP" --node-ip "$NODE_IP" --node-name "$NODE" \
     --provider-interface "$PROVIDER_IF"
```

`cloudx-setup-compute` installs libvirt, QEMU and Open vSwitch, points `ovn-controller` at the controller, creates `br-ex`, writes the worker configs and installs the `libvirt-python` binding. Its steps are listed in [../deb-install.md](../deb-install.md) section 4.2.

> **Lab note.** On a single-NIC machine, moving the NIC into `br-ex` takes the machine's address with it. The lab gave `br-ex` the address instead (a netplan file with the NIC as an Open vSwitch port and the IP on the bridge) and kept Wi-Fi for SSH, so a mistake could not cut the session. Do the same, or use the second NIC.

Make one of the two nodes a gateway for the routers' external ports:

```bash
sudo ovs-vsctl set open_vswitch . external_ids:ovn-cms-options=enable-chassis-as-gw     # infra02 only
```

Give the node its volume backend with the packaged script:

```bash
sudo cloudx-setup-storage --host "$NODE" --portal-ip "$NODE_IP" --device /dev/sdb --controller-ip "$CTRL_IP"
```

> **Lab note.** The lab machines have one disk with free space in the existing `ubuntu-vg`, and `cloudx-setup-storage` refuses a device that is mounted. The lab wrote the same settings by hand into `/etc/cloudx/block-store-worker/block-store-worker.conf` (`block_store_host`, `iscsi_export`, `iscsi_portal_ip`, `lvm_vg_name = ubuntu-vg`, `lvm_pv_name`) and let LVM create volumes in the existing group. With a spare disk, use the command above.

Start the services and check them:

```bash
sudo systemctl restart cloudx-compute-worker cloudx-metadata-proxy cloudx-block-store-worker
systemctl is-active cloudx-compute-worker cloudx-block-store-worker
```

Back on the controller, tell block storage which nodes serve LVM volumes. In `/etc/cloudx/block-store-api/block-store-api.conf`:

```ini
[DEFAULT]
block_store_lvm_hosts = infra02,infra03
```

```bash
sudo systemctl restart cloudx-block-store-api
cloudx volume-type create sc1 --backend lvm --disk-type sata_hdd --max-iops 100 --max-throughput 80
```

Every VM gets a root volume, so a volume type must exist before the first VM.

## 4. The first network and image

As in [../../SETUP.md](../../SETUP.md) section 6 and the [quickstart](../quickstart.md) steps 7 and 8: create `public-net` with its subnet and pool, a private `demo-net` with a router, upload a CirrOS image, create a key pair and a security group that allows SSH and ping.

## 5. Check the cluster

```console
$ cloudx hypervisor list
+---------+---------+----------+---------------+--------------------+------------------+-------------------+
| host    | name    | status   |   running_vms | cpu_percent_used   | memory_mb_used   | memory_mb_total   |
+=========+=========+==========+===============+====================+==================+===================+
| infra02 | infra02 | active   |             0 | None               | None             | None              |
| infra03 | infra03 | active   |             0 | None               | None             | None              |
+---------+---------+----------+---------------+--------------------+------------------+-------------------+
$ cloudx placement provider-list
$ sudo ovn-sbctl --columns=hostname,name list Chassis       # on the controller: one chassis per worker node
```

Both nodes must be `active`, both must appear as placement providers (that is where the scheduler picks from), and both must be OVN chassis. If a node is missing from `hypervisor list`, see the first lines of `journalctl -u cloudx-compute-worker`: a `ConnectionError` to `127.0.0.1` means the catalog still holds the default addresses (section 2, step 3).

## 6. Spread VMs over the nodes

By default the scheduler (`placement-api`) puts a new VM on the node with the most free memory. To force VMs apart, put them in a server group with the `anti-affinity` policy:

```bash
cloudx server-group create spread --policy anti-affinity
```

The CLI cannot yet name a server group when it creates a server, so create the servers through the API. Get a token and the compute address (the token is a short-lived JWT):

```bash
TOKEN=$(curl -s -X POST "http://$CTRL_IP:8005/auth/tokens" -H 'Content-Type: application/json' \
        -d '{"username":"admin","password":"<admin password>"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
for n in web-a web-b web-c; do
  curl -s -X POST "http://$CTRL_IP:8003/servers" -H "X-Auth-Token: $TOKEN" -H 'Content-Type: application/json' \
       -d "{\"name\":\"$n\",\"image\":\"cirros\",\"flavor\":\"tiny\",\"network\":\"demo-net\",\"server_group\":\"spread\"}" \
       | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("name"), d.get("status"), d.get("host"), d.get("detail",""))'
done
```

What the lab printed (with the scheduling fix deployed):

```
web-a BUILD infra03
web-b BUILD infra02
None None None {'message': 'No valid host was found', 'detail': 'No valid host: insufficient resources or filtered out'}
```

The first two members land on different nodes, and the third is refused with HTTP 409 because there is no node left that does not already hold a member. Without the fix, the third request was accepted and placed on a node that already had a member.

```bash
cloudx server list
```

## 7. A volume on one node, a VM on the other

Create a volume; it is placed on a node listed in `block_store_lvm_hosts`:

```bash
cloudx volume create shared-1 --size 1 --type sc1
cloudx -o json volume show shared-1 | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], d["host"])'
```

The lab got `available infra03`. Attach it to a VM on the other node (`web-b` ran on infra02):

```bash
cloudx volume attach shared-1 --server web-b
```

On the node that runs the VM, the disk is an iSCSI device that points at the storage node (output shortened; the domain and volume names are the lab's):

```console
$ sudo virsh domblklist mini-os-web-b-<id>
 Target   Source
--------------------------------------------------------------------------------------------------
 sda      /dev/ubuntu-vg/vol-0e6d5220f98247f78de1d246
 sdb      /dev/disk/by-path/ip-192.168.15.13:3260-iscsi-iqn.2026-10.io.cloudx:vol-edd8e97a7ea8475899b756de-lun-0
 hda      /var/lib/cloudx/compute/instances/<server-id>/seed.iso
```

`sda` is the root volume, a local LV on the VM's own node. `sdb` is the volume you attached, served by `192.168.15.13:3260` (infra03) over iSCSI. Detach and delete it when you are done:

```bash
cloudx volume detach shared-1
cloudx volume delete shared-1
```

## 8. What happens when something stops

These were tested on the same three machines on 2026-10-26 (the bugs they found were fixed in the 0.8.6 follow-ups: tap devices after a reboot, deleting a server in BUILD, TCP keepalive on RabbitMQ connections, no password in logs):

- A worker node restarted hard (`sysrq-b`) with a VM running: the node was back in about 70 seconds and the VM came back by itself.
- The storage node restarted with a volume attached: the iSCSI target was restored from its saved configuration, the initiator reconnected and the VM kept writing.
- The controller restarted hard: PostgreSQL, RabbitMQ and OVN data were intact, and all workers were consuming again about 85 seconds after the crash.
- Wi-Fi down on a worker for two minutes: no effect, because management, tunnels and the public network all ran over the cable.

Not tested: a real power cut (disk write caches), and many VMs at once.

## Clean up

```bash
cloudx server delete web-a && cloudx server delete web-b
cloudx server-group delete spread
```

Next: [auto-scaling](autoscaling.md), or add the optional services listed in [../architecture.md](../architecture.md).
