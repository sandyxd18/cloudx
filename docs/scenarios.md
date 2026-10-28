# Choose a deployment

CloudX runs on one machine or on several. The volume backend (where the disks of VMs and volumes live) decides most of what you need: how many machines, which disks, which network. This page lists the deployments the documentation covers, what each one needs and how much of it has been run. For the other services that have more than one backend (images, object storage, file shares) see [storage-backends.md](storage-backends.md).

| | Machines | Volume backend | Guide | Run on |
|---|---|---|---|---|
| A. One machine | 1 | LVM | [quickstart.md](quickstart.md) | Clean installs on 2026-10-22, 2026-10-26 and 2026-10-27, the last with the release packages |
| B. Several machines, LVM | 3 tested (controller and two nodes that run VMs and hold volumes) | LVM, exported over iSCSI between nodes | [tutorials/multi-node.md](tutorials/multi-node.md), then [deb-install.md](deb-install.md) sections 4 and 5 | Three bare-metal machines, 2026-10-24 to 2026-10-26 |
| C. Several machines, Ceph | 3 for Ceph, plus compute nodes (the tested lab had 6 in all) | Ceph RBD | [tutorials/ceph.md](tutorials/ceph.md) | A six-node lab, 2026-10-21 and 2026-10-22, with an earlier build; not repeated on 1.0.0 |
| D. LVM and Ceph together | – | Both | [storage-backends.md](storage-backends.md#using-lvm-and-ceph-together) | Not tested |

Everything runs on Ubuntu 24.04 amd64. `/dev/kvm` must exist on every machine that runs VMs (on a VM, turn on nested virtualization).

## A. One machine

Control plane, compute and storage on the same machine.

| | |
|---|---|
| Machines | 1 |
| CPU and RAM | 4 vCPU and 8 GB (the control plane alone uses about 4 GB) |
| Disks | A system disk of 40 GB or more, and a second, empty disk of 30 GB or more for volumes |
| Network interfaces | 2: one with the machine's address and internet access, one spare for the provider (public) network |
| Volume backend | LVM on the second disk. Volumes are exported over iSCSI to the same machine |
| Time | About 30 minutes |

This scenario has LVM only. Ceph needs at least three machines (see C), and CloudX has no procedure for Ceph on one machine.

## B. Several machines with LVM

A controller that holds the control plane, and worker nodes that run VMs and keep their volumes on local disks. A volume on one node can be attached to a VM on another node, over iSCSI.

| | |
|---|---|
| Machines | 3 in the tested layout: one controller and two worker nodes that both run VMs and hold volumes |
| Controller | 8 GB of RAM or more for the full `cloudx-controller` role. The tutorial also shows a trimmed set that ran on 3.7 GB |
| Worker nodes | A spare disk for volumes (an empty disk such as `/dev/sdb`, or free space in an existing volume group), and `/dev/kvm`. The lab nodes had 3.7 GB of RAM |
| Network interfaces | One shared network is enough for a lab (management, tunnels and provider network on the same cable). A second interface per node for the provider network is optional (`--provider-interface`) |
| Between nodes | Worker nodes must reach each other's iSCSI portal (`iscsi_portal_ip`) |
| Volume backend | LVM, one volume group per node |
| Time | About 2 hours, most of it `apt` |

The nodes do not share a disk: each volume lives on one node and the others reach it over iSCSI, on the same network as everything else. What that means when a VM has to move is in [storage-backends.md](storage-backends.md#sharing-volumes-between-nodes-and-moving-vms). The lab sent iSCSI over the management network. For production that is not a good idea in general: volume traffic then shares the network with RabbitMQ, the APIs and SSH, and one busy volume can slow the control plane down. Give iSCSI its own network (an interface or a VLAN) and pass that network's address of each storage node as `--portal-ip`. CloudX has no CHAP and no multipath, only an access list of initiator names per volume, and iSCSI is not encrypted, so that network should be closed to everything else. A separate iSCSI network was not tried. A node can also hold volumes without running VMs (the `cloudx-storage` role, [deb-install.md](deb-install.md) section 4.3), and services can be moved to other nodes ([deb-install.md](deb-install.md) section 5).

## C. Several machines with Ceph RBD

The same control plane, with volumes and root disks stored as RBD images in a Ceph cluster. QEMU opens the images over the network, so there is no iSCSI and any compute node can run any VM.

| | |
|---|---|
| Ceph nodes | 3 at least: three monitors and replica 3. They can be the controller and two other nodes. Each needs an empty disk for an OSD |
| Size of the tested nodes | 7 GB of RAM, with the control plane on the same machines. Smaller nodes were not tried |
| Compute nodes | Separate machines in the tested lab (three Ceph and control plane nodes, three compute nodes). Each needs `ceph-common`, the cluster's `ceph.conf`, the `client.cloudx` keyring and a libvirt secret (`cloudx-setup-ceph-secret`) |
| Networks | Management, and optionally a Ceph public network and a Ceph cluster network. Who joins which is in the table below |
| Ceph version | Install the cluster from the image `quay.io/ceph/ceph:v19.2.3`. Ubuntu 24.04's Ceph client packages are 19.2.3 and cannot read the keys that 19.2.7 hands out |
| Volume backend | Ceph RBD ([tutorials/ceph.md](tutorials/ceph.md) turns the LVM and NFS drivers off on the block store node) |
| Time | About 2 hours |

Which node joins which network when the Ceph networks are kept apart:

| Node | Management | Ceph public | Ceph cluster | Provider |
|---|---|---|---|---|
| Control plane node (with the Ceph `block-store-worker`) | yes | yes | no | no |
| Ceph nodes (monitors and OSDs) | yes | yes | yes | no |
| Compute nodes | yes | yes | no | yes, where VMs reach the provider network |

The Ceph public network carries everything between Ceph and its clients: the monitors and OSDs, the block store worker (it creates and converts RBD images) and QEMU on each compute node (it opens the images itself). The cluster network carries only OSD to OSD traffic (replication, recovery), so only the Ceph nodes need it. A node that runs no Ceph client and no Ceph daemon needs neither. `ceph.conf` on every client lists monitor addresses on the public network.

Losing one of three Ceph nodes leaves the cluster running but degraded: there is no fourth host to create the third copy on. Not covered: images in Ceph (the image service still stores files or MinIO), Ceph Object Gateway for object storage, CephFS for file shares, and how instance HA fences a node whose volumes are RBD images.

## D. LVM and Ceph together

One cloud with volume types on both backends. This has not been run, and reading the code raises a routing question that has to be answered first. See [storage-backends.md](storage-backends.md#using-lvm-and-ceph-together).

## Older installs

Nodes installed from the 0.8.x release tarball upgrade to the packages with the runbook in [deb-install.md](deb-install.md) section 7. The tarball guides ([tarball-install.md](tarball-install.md), [multi-node-deployment.md](multi-node-deployment.md), [two-node-lvm-lab.md](two-node-lvm-lab.md), [single-node-lab.md](single-node-lab.md)) are kept as history and are no longer the way to install.

## What none of these provide

A highly available control plane. PostgreSQL, RabbitMQ and OVN central run as a single instance on the controller, and there is no load balancer in front of the APIs. [production-readiness.md](production-readiness.md) says what exists and what does not.
