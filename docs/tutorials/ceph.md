# Tutorial: Ceph RBD as the block storage backend

You will build a small Ceph cluster with `cephadm`, create the pools and a cephx user for CloudX, point the block store and the compute nodes at it, and boot a VM from an RBD root volume. About two hours.

> **Status: not re-run on the current hardware.** These are the steps that were run on the six-node development lab on 2026-10-21 and 2026-10-22 (three Ceph nodes with one 50 GB disk each, three compute nodes). On that cluster a VM booted from an RBD root volume (CirrOS reached the login prompt and got DHCP) and a data volume attached and detached. The three machines of the current lab have 3.7 GB of RAM and a single disk each, which is too little for Ceph, so nothing here has been repeated on them. If a step fails, please open an issue with the output.

## What you need

- Three nodes for Ceph (they can be the controller and two other nodes), each with an **empty** disk for an OSD. The lab used 7 GB nodes that also ran the control plane; smaller machines were not tried. Three nodes give three monitors and replica 3; losing a node leaves the cluster degraded, because there is no fourth host to re-create the third copy.
- A CloudX cloud with compute nodes already working with LVM, or built for Ceph from the start ([multi-node.md](multi-node.md)).
- Optional: a separate network for Ceph replication (`--cluster-network` below).

Variables used below (adjust):

```bash
export CEPH_MON_IP=172.18.0.2        # this node's address on the Ceph public network
export CEPH_CLUSTER_NET=172.19.0.0/16
```

## 1. Pin the Ceph version

This is the one thing that will bite you. Ubuntu 24.04 ships Ceph client packages 19.2.3 (`ceph-common`, `librbd1`). `cephadm` pulls the moving image tag `v19`, which later became 19.2.7. Version 19.2.7 hands out cephx keys in a newer format (they start with `Ag...`) that the 19.2.3 clients cannot read, so `rbd`, `qemu-img` and libvirt all fail to authenticate. Bootstrap with the image pinned to the client version:

```
quay.io/ceph/ceph:v19.2.3
```

Do not loosen cephx settings to work around a mismatch. If you already bootstrapped with the moving tag, check `ceph versions` and rebuild.

## 2. Bootstrap the cluster

On every Ceph node:

```bash
sudo apt update
sudo apt install -y cephadm podman lvm2 chrony
```

On the first node:

```bash
sudo cephadm --image quay.io/ceph/ceph:v19.2.3 bootstrap \
     --mon-ip "$CEPH_MON_IP" --cluster-network "$CEPH_CLUSTER_NET" \
     --ssh-user ubuntu --skip-monitoring-stack --allow-fqdn-hostname \
     --initial-dashboard-user admin --initial-dashboard-password '<a password>' --dashboard-password-noupdate
sudo apt install -y ceph-common          # the host's ceph and rbd commands (19.2.3)
sudo ceph versions                       # every daemon must say 19.2.3
```

Let the first node reach the others over SSH with the key `cephadm` created, then add them and place the monitors:

```bash
# on each other node: append the content of /etc/ceph/ceph.pub (from the first node) to ~/.ssh/authorized_keys
sudo ceph orch host add infra02 <infra02 ceph address> --labels _admin
sudo ceph orch host add infra03 <infra03 ceph address> --labels _admin
sudo ceph orch apply mon --placement="3 infra01 infra02 infra03"
sudo ceph orch apply mgr --placement="2 infra01 infra02"
```

On nodes that also run other services, keep OSD memory small and recovery gentle:

```bash
sudo ceph config set osd osd_memory_target 1073741824
sudo ceph config set osd osd_max_backfills 1
sudo ceph config set osd osd_recovery_max_active 1
```

Add one OSD per node on its empty disk, then check:

```bash
for h in infra01 infra02 infra03; do sudo ceph orch daemon add osd $h:/dev/sdb; done
sudo ceph -s                              # HEALTH_OK, 3 osds up and in
```

## 3. Pools and the CloudX user

```bash
for p in volumes images vms; do
  sudo ceph osd pool create $p 32 32 replicated
  sudo ceph osd pool application enable $p rbd
  sudo rbd pool init $p
done

sudo ceph auth get-or-create client.cloudx \
     mon "profile rbd" \
     osd "profile rbd pool=volumes, profile rbd pool=images, profile rbd pool=vms" \
     mgr "profile rbd pool=volumes, profile rbd pool=images, profile rbd pool=vms" \
     -o /etc/ceph/ceph.client.cloudx.keyring
sudo chmod 600 /etc/ceph/ceph.client.cloudx.keyring
sudo rbd --id cloudx --keyring /etc/ceph/ceph.client.cloudx.keyring ls volumes     # empty, no error
```

## 4. The block store node (usually the controller)

The node that runs `block-store-worker` needs the Ceph client, the cluster's `ceph.conf`, the keyring, and `qemu-utils` (the worker converts images to RBD with `qemu-img`):

```bash
sudo apt install -y ceph-common qemu-utils
# /etc/ceph/ceph.conf and /etc/ceph/ceph.client.cloudx.keyring: copy them from the first Ceph node (keyring mode 600)
```

In `/etc/cloudx/cloudx.conf` (`[DEFAULT]`):

```ini
storage_lvm_driver = false
storage_nfs_driver = false
storage_ceph_rbd_driver = true
ceph_pool = volumes
ceph_conf = /etc/ceph/ceph.conf
ceph_keyring = /etc/ceph/ceph.client.cloudx.keyring
ceph_args = --id cloudx
ceph_user = cloudx
```

`ceph_user` matters because `qemu-img` does not read `ceph_args`; the worker passes the user to both `rbd` and `qemu-img` from it. The lab used this for a Ceph-only cluster; the settings above switch off the LVM and NFS drivers on that node. Restart `cloudx-block-store-worker` and `cloudx-block-store-api`.

Create the volume type:

```bash
cloudx volume-type create ceph-rbd --backend ceph --disk-type sata_hdd
cloudx volume create probe --size 1 --type ceph-rbd
sudo rbd --id cloudx --keyring /etc/ceph/ceph.client.cloudx.keyring ls volumes      # the new image appears
```

## 5. The compute nodes

QEMU opens RBD disks itself, with the cephx key, so every compute node needs the client packages, the config and the key, plus a libvirt secret that holds it:

```bash
sudo apt install -y ceph-common qemu-block-extra
# copy /etc/ceph/ceph.conf and /etc/ceph/ceph.client.cloudx.keyring from the first Ceph node,
# keyring mode 640 and group libvirt-qemu (or kvm)
sudo cloudx-setup-ceph-secret          # user cloudx, keyring /etc/ceph/ceph.client.cloudx.keyring
sudo systemctl restart cloudx-compute-worker
```

The script defines the libvirt secret from the keyring, then writes `ceph_user` and `ceph_secret_uuid` to `/etc/cloudx/compute-worker/compute-worker.conf`. It is safe to run again. See [../deb-install.md](../deb-install.md) section 4.2 step 5.

## 6. Boot a VM from RBD

```bash
cloudx server create vm-rbd --image cirros --flavor tiny --network demo-net --volume-size 2
cloudx server show vm-rbd                  # ACTIVE
sudo rbd --id cloudx --keyring /etc/ceph/ceph.client.cloudx.keyring ls volumes     # the root volume
sudo qemu-img info "rbd:volumes/<image name>:conf=/etc/ceph/ceph.conf:id=cloudx"    # raw, 2 GiB
cloudx volume create data-1 --size 1 --type ceph-rbd
cloudx volume attach data-1 --server vm-rbd
cloudx volume detach data-1
```

For the root volume, CloudX creates an RBD image in `volumes` and converts the CirrOS image into it. Both root and data volumes are reached by QEMU directly over the network; no iSCSI is involved, so any compute node can run the VM.

## LVM and Ceph volumes in one cloud

The steps above switch off the LVM driver on the block-store node. To serve LVM and RBD volumes side by side, keep both drivers on the API and give the Ceph driver to every block-store worker: workers also consume a shared queue, and a worker without the Ceph driver fails the Ceph volume requests it picks up. [ha-control-plane.md](ha-control-plane.md) section 5 has the settings and the test results.

## What this does not cover

- The `images` and `vms` pools are created for later use; nothing in this tutorial uses them, and the image service still stores files (or MinIO).
- CephFS for the file store and RGW for object storage were not tried.
- Instance HA: its fencing step is built around iSCSI access ([../ha.md](../ha.md)); how it behaves with RBD volumes was not tested.
- Performance. The lab disks were small virtual disks.

## Troubleshooting

- `rbd` or `qemu-img` fails to authenticate: the client and the cluster versions differ (section 1), or the keyring is not readable by the user running the command.
- `Domain` fails to start with an RBD authentication error: the libvirt secret is missing or stale on that compute node; run `cloudx-setup-ceph-secret` again and restart `cloudx-compute-worker`.
- A volume created from an image is not bootable: `qemu-img` is missing on the block store node, or it ran without the Ceph user; check `ceph_user` and `qemu-utils`.
