# CloudX Documentation

Start with `../README.md` (overview) and [quickstart.md](quickstart.md) (a first VM in about 30 minutes). The documents in this folder are grouped below. The documentation is in English; the only exception is part of the design notes listed at the bottom.

CloudX runs natively under systemd on Ubuntu 24.04. There is no Docker Compose deployment any more; the few older pages that mention it say so.

## Installing and operating
| Document | Contents |
|---|---|
| `quickstart.md` | Start here: all-in-one install on one Ubuntu 24.04 machine from the `.deb` packages, first VM and floating IP in about 30 minutes |
| `tutorials/` | Step-by-step tutorials: [three-node cloud](tutorials/multi-node.md), [auto-scaling](tutorials/autoscaling.md), [Ceph RBD](tutorials/ceph.md), [Kubernetes](tutorials/kubernetes.md), [dashboard tour](tutorials/dashboard-tour.md). Each says at the top whether it was run and where |
| `deb-install.md` | Reference for the `.deb` packages: building them, installing by role, spreading services across nodes, catalog, rotating passwords, upgrading tarball nodes, rollback |
| `../SETUP.md` | Setup guide: choosing a layout, preparing PostgreSQL, RabbitMQ and OVN, the admin account, the first network |
| `tarball-install.md` | Legacy: installation from the release tarball (controller, storage node, compute node) |
| `systemd-deployment-guide.md` | Native systemd and the `/etc/cloudx/` configuration |
| `multi-node-deployment.md` | Multi-node deployment and how to test it |
| `single-node-lab.md` | Scope of the single-node lab |
| `two-node-lvm-lab.md` | Lab where compute and storage nodes both run VMs and serve LVM volumes (iSCSI between nodes) |
| `fase5-production-readiness.md` | What exists for multi-node, backups, monitoring and migrations, and what does not |
| `user-guide-cli.md` | `cloudx` CLI guide |
| `cleanup.md`, `troubleshooting*.md` | Resource cleanup and troubleshooting |
| `../services/migrations/README.md` | Database migrations (Alembic), `cloudx-manage db sync` |
| `releases/` | Release notes per version, with upgrade steps |

## Architecture and code
| Document | Contents |
|---|---|
| `architecture.md` | High-level architecture |
| `code-structure.md` | Code layout, rules, tests, error handling, running commands |
| `feature-comparison.md`, `roadmap-openstack-parity.md` | Feature comparison with OpenStack and the roadmap |

## Compute
`real-vm.md`, `libvirt.md`, `flavor.md`, `keypair.md`, `console.md`, `cloud-init.md`, `placement-lite.md`,
`boot-from-volume.md`, `root-volume.md`, `ha.md` (instance HA), `container.md`, `k8s-engine.md`, `reservation.md`

## Networking
`ovn.md`, `ovn-dhcp.md`, `ovn-localnet.md`, `ovn-acl.md`, `ovs-ovn-vm-binding.md`, `br-ex.md`, `provider-network.md`,
`multi-network.md`, `ipam.md`, `floating-ip.md`, `security-group.md`, `security-group-ovn-acl.md`,
`metadata-service.md`, `metadata-proxy.md`, `vpn-site-to-site.md`, `bgp-dynamic-routing.md`, `dns.md`

## Storage and data
`storage-backend.md` (images), `snapshot.md` (volumes and backups), `file-store.md` (NFS shares), `database.md` (database
instances), `messaging.md` (queues)

## By phase
`fase5-production-readiness.md`, `fase5-telemetry-billing-events.md`, `fase6-orchestration-vpc-bgp.md`

## Design notes
`superpowers/specs/` and `superpowers/plans/` hold the design documents and implementation plans written while the
features were built. They are working notes, not user documentation, and some of them are still in Indonesian.
