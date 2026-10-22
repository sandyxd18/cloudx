# CloudX Documentation

Start with `../README.md` (overview) and `../SETUP.md` (installation). The documents in this folder are grouped below.

> **Docker note.** CloudX now runs natively under systemd; the `docker-compose*.yml` files and Dockerfiles have been
> removed. Some older documents still contain `docker compose` commands from the earlier era: `fase5-production-readiness`
> (17 occurrences), `troubleshooting` (8), `ovn` (6), and a few others with one or two. For current steps use
> `../SETUP.md`, `systemd-deployment-guide.md`, and `multi-node-deployment.md`.

## Installing and operating
| Document | Contents |
|---|---|
| `../SETUP.md` | Installation from scratch (controller, storage node, compute node), admin account |
| `systemd-deployment-guide.md` | Native systemd and the `/etc/cloudx/` configuration |
| `deb-install.md` | Installing with `.deb` packages (`apt`, one package per service, nginx front, service catalog) and upgrading tarball nodes; written in Indonesian; the upgrade runbook is verified on all six lab nodes (auth, two infra, three compute; running VMs survived the compute upgrades); rollback is verified on one of them; a clean install is not |
| `multi-node-deployment.md` | Multi-node deployment and how to test it |
| `single-node-lab.md` | Scope of the single-node lab |
| `two-node-lvm-lab.md` | Lab where compute and storage nodes both run VMs and serve LVM volumes (iSCSI between nodes) |
| `user-guide-cli.md` | `cloudx` CLI guide |
| `cleanup.md`, `troubleshooting*.md` | Resource cleanup and troubleshooting |
| `../services/migrations/README.md` | Database migrations (Alembic), `make migrate` |

## Architecture and code
| Document | Contents |
|---|---|
| `architecture.md` | High-level architecture |
| `code-structure.md` | Code layout, rules, tests, error handling, running commands |
| `feature-comparison.md`, `roadmap-openstack-parity.md` | Feature comparison and roadmap |

## Compute
`real-vm.md`, `libvirt.md`, `flavor.md`, `keypair.md`, `console.md`, `cloud-init.md`, `placement-lite.md`,
`boot-from-volume.md`, `root-volume.md`

## Networking
`ovn.md`, `ovn-dhcp.md`, `ovn-localnet.md`, `ovn-acl.md`, `ovs-ovn-vm-binding.md`, `br-ex.md`, `provider-network.md`,
`multi-network.md`, `ipam.md`, `floating-ip.md`, `security-group.md`, `security-group-ovn-acl.md`,
`metadata-service.md`, `metadata-proxy.md`, `vpn-site-to-site.md`, `bgp-dynamic-routing.md`, `k8s-engine.md`

## Storage
`storage-backend.md` (images), `snapshot.md` (volumes and backups)

## By phase
`fase5-production-readiness.md`, `fase5-telemetry-billing-events.md`, `fase6-orchestration-vpc-bgp.md`
