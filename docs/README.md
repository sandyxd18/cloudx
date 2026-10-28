# CloudX Documentation

Start with `../README.md` (overview) and [quickstart.md](quickstart.md) (a first VM in about 30 minutes). The documents in this folder are grouped below. All documentation is in English.

CloudX runs natively under systemd on Ubuntu 24.04. There is no Docker Compose deployment any more; the few older pages that mention it say so.

## Installing and operating
| Document | Contents |
|---|---|
| `scenarios.md` | Which deployment fits: one machine, several machines with LVM or with Ceph RBD, what each needs (machines, disks, NICs) and what has been run |
| `quickstart.md` | Start here: all-in-one install on one Ubuntu 24.04 machine from the `.deb` packages, first VM and floating IP in about 30 minutes |
| `tutorials/` | Step-by-step tutorials: [three-node cloud](tutorials/multi-node.md), [auto-scaling](tutorials/autoscaling.md), [Ceph RBD](tutorials/ceph.md), [Kubernetes](tutorials/kubernetes.md), [dashboard tour](tutorials/dashboard-tour.md). Each says at the top whether it was run and where |
| `deb-install.md` | Reference for the `.deb` packages: building them, installing by role, spreading services across nodes, catalog, rotating passwords, upgrading tarball nodes, rollback |
| `../SETUP.md` | Setup guide: choosing a layout, preparing PostgreSQL, RabbitMQ and OVN, the admin account, the first network |
| `storage-backends.md` | Services with more than one backend (block store, images, object store, file store, backups): how each is chosen, what it needs, what has been run |
| `production-readiness.md` | What exists for multi-node, backups, monitoring and migrations, and what does not |
| `user-guide-cli.md` | `cloudx` CLI guide |
| `cleanup.md`, `troubleshooting*.md` | Resource cleanup and troubleshooting |
| `../services/migrations/README.md` | Database migrations (Alembic), `cloudx-manage db sync` |
| `releases/` | Release notes per version, with upgrade steps |

## Older pages (tarball era)
Kept as history. New installs use the packages above.

| Document | Contents |
|---|---|
| `tarball-install.md` | Installation from the release tarball (controller, storage node, compute node) |
| `systemd-deployment-guide.md` | Native systemd with the tarball's `install-systemd.sh`; the layout of `/etc/cloudx/` still applies to the packages |
| `multi-node-deployment.md` | Two VirtualBox VMs, one controller and one compute node |
| `two-node-lvm-lab.md` | Two nodes that both run VMs and serve LVM volumes |
| `single-node-lab.md` | Scope of the first single-host VM backend (out of date) |

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
`storage-backends.md` (all backends), `image-storage.md` (images), `snapshot.md` (volumes and backups), `file-store.md` (NFS shares), `database.md` (database
instances), `messaging.md` (queues)

## Observability and orchestration
`telemetry-billing-events.md` (telemetry, billing, events), `orchestration-vpc-bgp.md` (multi-node scheduling,
auto-scaling, VPC peering and BGP)
