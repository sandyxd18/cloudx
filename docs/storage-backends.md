# Storage backends

Some CloudX services can keep their data in more than one place. This page lists them, says how the backend is chosen, what each choice needs, and what has been run. How many machines and disks a whole deployment needs is in [scenarios.md](scenarios.md).

## Overview

| Service | Backends | Chosen by | Documented in | Run |
|---|---|---|---|---|
| Block store (volumes, root disks) | LVM (iSCSI between nodes), Ceph RBD, NFS | The volume type (`--backend`), and three switches in `cloudx.conf` | [quickstart.md](quickstart.md), [tutorials/multi-node.md](tutorials/multi-node.md), [tutorials/ceph.md](tutorials/ceph.md) | LVM on one machine and on three. Ceph on a six-node lab, with an earlier build. NFS: no. Mixed: no |
| Compute (root disk of a VM) | LVM block device, RBD image, qcow2 file | Detected from the path of the volume | – | Follows the block store: LVM and RBD |
| Image | Files, or MinIO (S3) | `image_storage_backend` in `image-api.conf`; one for the whole service | [image-storage.md](image-storage.md) | Files. MinIO: no run recorded |
| Object store | Local directory (`filesystem`), or S3 (MinIO, AWS S3, Ceph Object Gateway) | The bucket type of each bucket | See [Object store](#object-store) below | No run recorded |
| File store | NFS-Ganesha on a local path | – | [file-store.md](file-store.md) | One node |
| Volume backups | MinIO (S3) | – | [snapshot.md](snapshot.md) | No run recorded |

Services that have one backend only: DNS (BIND), containers (Docker), networking (OVN and Open vSwitch), and virtualization (libvirt and QEMU). The load balancer uses OVN for L4 and HAProxy for L7 listeners; that is not a choice.

## Block store

Three drivers exist. Each is a switch in `/etc/cloudx/cloudx.conf`, read by `block-store-api` and by `block-store-worker`:

| Switch | Default in the code | In the packaged `cloudx.conf` |
|---|---|---|
| `storage_lvm_driver` | `false` | `true` |
| `storage_ceph_rbd_driver` | `false` | not set |
| `storage_nfs_driver` | `true` | not set |

A volume belongs to a volume type, and the type names its backend:

```bash
cloudx volume-type create gp2 --backend lvm --disk-type sata_ssd --max-iops 3000 --max-throughput 125
cloudx volume-type create ceph-rbd --backend ceph --disk-type sata_hdd
```

The API refuses a volume type whose driver is switched off (`LVM driver is disabled (STORAGE_LVM_DRIVER=false)`, and the same for Ceph and NFS).

| | LVM | Ceph RBD |
|---|---|---|
| What stores the data | A volume group on a disk of each storage node | A Ceph cluster (pools `volumes`, `images`, `vms`) |
| Machines | One, or several storage nodes | Three Ceph nodes at least |
| Per node setup | `cloudx-setup-storage` on every node that holds volumes | `ceph-common`, `ceph.conf`, the `client.cloudx` keyring on the block store node and on every compute node, and `cloudx-setup-ceph-secret` on the compute nodes |
| How a VM reaches the volume | Local block device, or iSCSI from the node that holds it | QEMU opens the RBD image over the network, no iSCSI |
| Where a volume lives | On one node: the API picks the storage node with the least allocated space from `block_store_lvm_hosts` | In the cluster, on no particular node |
| Snapshots and clones | LVM snapshots and copies | RBD snapshots and clones |
| Guide | [quickstart.md](quickstart.md), [tutorials/multi-node.md](tutorials/multi-node.md) | [tutorials/ceph.md](tutorials/ceph.md) |

NFS volumes exist in the code and the driver is on by default. The package guides do not cover them (only [tarball-install.md](tarball-install.md) does) and they have not been run on this release. Do not create a volume type with `--backend nfs` until that is done.

The compute side needs no setting for the backend: it looks at the path of the volume and builds an LVM, RBD or file disk for libvirt.

### Using LVM and Ceph together

Not tested. Do not rely on it yet: use one backend per cloud.

What the code shows, and what the test has to settle:

- Volume types carry the backend, so one cloud can have an LVM type and a Ceph type, and the API accepts both when both drivers are on.
- A new LVM volume gets a storage node (`block_store_lvm_hosts`), and its requests go to that node's own queue. A Ceph volume gets no node, so its requests go to the queue named `block-store`, which every `block-store-worker` reads.
- A worker does not check that the driver of the backend named in a request is switched on at its own node.
- So in a cloud with nodes that serve LVM only and one node with Ceph, a Ceph request may reach a node that has no Ceph configuration, and the volume would end up in `ERROR`. This is a reading of the code, not an observation.

The test should answer: does a Ceph request reach the wrong worker, what happens to LVM and Ceph volumes next to each other (create, attach, snapshot, clone, delete, boot from volume), and what the documentation should tell the operator to configure on each node. The multi-node requirements for this test are in [scenarios.md](scenarios.md), scenario D once it has been run.

## Image storage

One backend for the whole image service, files by default or MinIO. See [image-storage.md](image-storage.md). Images are not stored in Ceph.

## Object store

Each bucket has a bucket type, and the type names the driver, so the two drivers can run side by side in one cloud:

```bash
cloudx object-store type create local-files --driver filesystem     # files under /var/lib/cloudx/object-store (config key "root")
cloudx object-store type create minio --driver s3 \
  --config '{"endpoint_url": "http://<minio-host>:9000", "access_key": "<key>", "secret_key": "<secret>", "use_ssl": false}'
cloudx object-store type list
```

The `s3` driver is meant for MinIO, AWS S3 and Ceph Object Gateway. Only the S3 protocol is used. Ceph Object Gateway was not tried, and no run of either driver is recorded for this release.

## File store

NFS shares exported by NFS-Ganesha from a local path on the node named in `file_store_host`. CephFS was not tried. See [file-store.md](file-store.md).

## Volume backups

Backups of volumes (and of database instances) are uploaded to a bucket named `cloudx-backups` on a MinIO that you run. For LVM volumes the worker exports with `qemu-img`, for Ceph volumes with `rbd export`. CloudX does not install MinIO. See [snapshot.md](snapshot.md) and [production-readiness.md](production-readiness.md). Backups of the PostgreSQL database are not provided.
