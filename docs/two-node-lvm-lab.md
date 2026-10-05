# Two-Node LVM Lab (compute-node + storage-node)

Scenario: `storage-node` runs VMs too, and both `compute-node` and `storage-node` serve volumes from LVM on their own
`/dev/sdb`. A VM can use volumes from either node.

```
controller (192.168.10.10)   APIs, conductor, PostgreSQL, RabbitMQ, OVN central
compute-node (…11)  cloudx-compute-01   compute-worker + block-store-worker (vg-cloudx on /dev/sdb)
storage-node (…12)  cloudx-storage-01   compute-worker + block-store-worker (vg-cloudx on /dev/sdb)
```

## What the lab needs

The lab nodes already have the resources (4 vCPU / 8 GB each, nested virtualisation, a 30 GB `/dev/sdb` per data node,
so 60 GB of LVM in total). What is different from the 3-node layout in `SETUP.md`:

| Node | Change |
|---|---|
| storage-node | gets the compute stack (libvirt, OVS, ovn-controller, `compute-worker`, `metadata-proxy`); its `/dev/sdb` stops being an ext4 filesystem for NFS/MinIO and becomes an LVM physical volume |
| compute-node | `/dev/sdb` (empty) becomes an LVM physical volume; also runs a `block-store-worker` |
| controller | knows the two node names (`BLOCK_STORE_LVM_HOSTS`, `COMPUTE_HOSTS`) |

NFS is not used by this scenario: LVM volumes are local to the node that holds them, and are shared with the other
node over iSCSI.

## How it works

- **Host-addressed RPC.** Every worker consumes two queues: its own (`compute.<host>`, `block-store.<host>`) and the
  shared one (`compute`, `block-store`). The APIs send a message to the node it concerns; the shared queue only carries
  what has no node (backups in object storage, servers/volumes created before the upgrade).
- **Where things go.** A new volume lands on the storage node with the least allocated GB (`volume.host`); snapshots and
  clones stay on the node of their source. A new server lands on the compute node with the fewest servers
  (`server.host`).
- **Attaching a volume.** If the volume is on the VM's node it is attached through its local LV path. Otherwise the
  compute-worker asks the storage node to export the LV (one iSCSI target per volume, with an ACL for that compute
  node's initiator only), logs in with `iscsiadm`, and hands the resulting `/dev/disk/by-path/...` device to libvirt.
  Detach, VM delete and a failed attach log out and withdraw the export.
- **Reconcilers** only judge the LVs of their own node, so a volume on the other node is never marked missing.

## Setup

Take a database dump first (`pg_dump`), and note that the NFS volumes and the MinIO data currently on the storage
node's `/dev/sdb` are lost when the disk is re-purposed: delete or back up the volumes you care about, and move
`/srv/cloudx/minio-data` to the root disk (or recreate the buckets).

1. **Storage node: free `/dev/sdb`.**
   ```bash
   sudo systemctl stop cloudx-block-store-worker minio nfs-kernel-server     # whichever of them run here
   sudo rsync -a /srv/cloudx/minio-data/ /var/lib/cloudx/minio-data/          # keep MinIO on the root disk
   sudo umount /srv/cloudx && sudo sed -i '\#/srv/cloudx#d' /etc/fstab
   ```
   On the compute node remove the NFS mount of `/var/lib/cloudx/volumes` (`umount`, then delete its `/etc/fstab` line) and
   point MinIO's data directory at `/var/lib/cloudx/minio-data`.
2. **Both data nodes: prepare LVM + iSCSI.**
   ```bash
   # storage-node (use --wipe because the disk still carries the old ext4 signature)
   sudo ./scripts/prepare-lvm-node.sh --host cloudx-storage-01 --portal-ip 192.168.10.12 --device /dev/sdb --wipe
   # compute-node
   sudo ./scripts/prepare-lvm-node.sh --host cloudx-compute-01 --portal-ip 192.168.10.11 --device /dev/sdb
   ```
3. **Storage node: become a compute node too.**
   ```bash
   sudo CONTROLLER_IP=192.168.10.10 NODE_IP=192.168.10.12 NODE_NAME=cloudx-storage-01 PROVIDER_IF=<provider nic> \
        ./scripts/setup-compute-node.sh
   ```
   then install the CloudX services as in `SETUP.md` section 4.4 and
   `sudo systemctl enable --now cloudx-compute-worker cloudx-metadata-proxy`. Check `COMPUTE_HOST` on both nodes
   (`systemctl show cloudx-compute-worker -p Environment`): they must differ.
4. **Controller.** In `/etc/cloudx/block-store-api/block-store-api.conf` set
   `block_store_lvm_hosts = cloudx-compute-01,cloudx-storage-01`, and in `/etc/cloudx/compute-api/compute-api.conf`
   `compute_hosts = cloudx-compute-01,cloudx-storage-01` (the names are the `BLOCK_STORE_HOST` / `COMPUTE_HOST` of the
   workers). Capacity checks still use one cluster-wide total: set `PLACEMENT_TOTAL_VCPU`, `PLACEMENT_TOTAL_RAM_MB` and
   `PLACEMENT_TOTAL_DISK_GB` for compute-api to the sum over both compute nodes. Then:
   ```bash
   sudo make migrate                                   # adds block_store_volumes.host
   sudo systemctl restart cloudx-block-store-api cloudx-compute-api cloudx-conductor
   ```
5. **Start the workers** (`sudo systemctl restart cloudx-block-store-worker` on both data nodes) and create an LVM volume
   type if there is none: `cloudx volume-type create lvm-ssd --backend lvm --disk-type sata_ssd`.

## Verify

```bash
vgs && sudo targetcli ls /iscsi                       # on each data node: vg-cloudx exists, no stale targets
cloudx hypervisor list                                # both nodes registered
cloudx volume create v1 --size 2 --type lvm-ssd      # repeat: volumes alternate between the nodes
cloudx volume show v1 | grep -i host                  # the node that owns the LV
cloudx server create vm1 --image <image> --flavor tiny --network private-net
cloudx volume attach v1 --server vm1                  # works whichever node holds v1 or vm1
sudo iscsiadm -m session                              # on the VM's node: a session if v1 is on the other node
cloudx volume detach v1                               # the session and the target disappear
```

## Limits

- A volume is only as available as its node: if the storage node is down, VMs using its volumes (root volumes included)
  lose their disk. There is no replication, multipath or CHAP (only the initiator ACL).
- A server's node is chosen when it is created (fewest servers; capacity is not compared per node). Live migration of a
  VM with a remote iSCSI volume attached is not supported.
- Snapshots, clones and consistency groups stay on one node (a group spanning two nodes is refused with 409);
  `migrate_volume` changes backend on the same node, not the node.
- Servers created before the upgrade have `host = single-node` and keep using the shared queue; delete and recreate them
  so they get a node.
- Two block-store workers share the `block-store` queue for host-less volumes (the old NFS ones): do not keep such volumes
  in a two-node setup.
