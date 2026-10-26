# CloudX

CloudX is a private cloud platform for Ubuntu 24.04. It runs VMs on **libvirt + QEMU/KVM**, networks them with **OVN/Open vSwitch**, and ties the services together with **RabbitMQ** and **PostgreSQL**. Every service is a systemd unit installed from a `.deb` package. It comes with a command line client (`cloudx`) and a web dashboard built on the AWS Cloudscape Design System.

The APIs follow the shape of OpenStack's (an identity service, compute, networking, block storage, images, orchestration and so on) without being compatible with them. The [feature comparison](docs/feature-comparison.md) says where CloudX matches OpenStack, where it differs and what it leaves out.

> **Status.** CloudX is heading to version 1.0.0. It is built and run by one maintainer, and it has been tested on a six-node lab and on single machines, not on production workloads. Read [Known limitations](docs/quickstart.md#known-limitations) before relying on it, and see [SECURITY.md](SECURITY.md) for how to report a problem.

## What you need

- Ubuntu 24.04 amd64 on every node. No other distribution is supported.
- `/dev/kvm` on the compute nodes (on a VM, nested virtualization).
- For a first try: one machine with 4 vCPU, 8 GB RAM, two network interfaces and a spare 30 GB disk.

## Install

Download the `.deb` files and `SHA256SUMS` from the [releases page](https://github.com/sandyxd18/cloudx/releases), check them, and install:

```bash
sha256sum -c SHA256SUMS --ignore-missing
```

Which packages go on which node, and what to prepare first, is in the guides (`apt install ./file.deb` resolves the dependencies between the files you name together, so no apt repository is needed):

| Guide | For |
|---|---|
| [docs/quickstart.md](docs/quickstart.md) | Everything on one machine: a VM with a floating IP in about 30 minutes |
| [SETUP.md](SETUP.md) | Choosing a layout, preparing PostgreSQL, RabbitMQ and OVN, the admin account, the first network |
| [docs/deb-install.md](docs/deb-install.md) | Installing by role (controller, compute, storage), spreading services over nodes, upgrades and rollback |

## What is in it

| Area | Services |
|---|---|
| Identity | `auth-api`: users, projects, domains, roles, access keys, service catalog |
| Compute | `compute-api`, `conductor`, `compute-worker`: VMs, flavors, key pairs, server groups, scheduling through `placement-api` |
| Networking | `networking-api`: networks, subnets, routers, floating IPs, security groups, VPC peering, VPN, BGP; `metadata-api`, `metadata-proxy`; `load-balancer-api`, `lb-worker` (L4 and L7); `dns-api`, `dns-worker` |
| Storage | `block-store-api`, `block-store-worker` (LVM or Ceph RBD volumes, snapshots, backups); `image-api`; `object-store-api` (S3-style); `file-store-api` (NFS shares); `key-manager-api` |
| Orchestration | `orchestration-api` (stacks and auto-scaling), `k8s-engine-api` (K3s clusters), `container-api` (single containers) |
| Platform services | `database-api` (PostgreSQL and MariaDB instances), `messaging-api` (queues), `reservation-api` (capacity leases), `ha-api` and `ha-watchdog` (instance recovery), `reaper` |
| Operations | `telemetry-api`, `billing-api`, `event-api` with workers; `/metrics` on every API |
| Interfaces | `cloudx` CLI, web dashboard on port 8080 |

[docs/architecture.md](docs/architecture.md) describes how they fit together, and [docs/deb-install.md](docs/deb-install.md) section 1 lists every package and port.

## The CLI

```bash
cloudx auth login --username admin --auth-url http://<controller>:8005
cloudx server create web-1 --image cirros --flavor tiny --network demo-net --key-name demo-key
cloudx floating-ip create public-net
cloudx --help                                   # all command groups
cloudx server --help                            # options of one group
```

`cloudx --help` lists the command groups:

```
alarm  auth  backup  bgp-peer  cluster  container  database  dns  event  file-store  flavor  floating-ip  ha
host  hypervisor  image  k8s-template  keypair  load-balancer  network  object-store  placement  port  queue
reservation  role-assignment  router  scaling-group  secret  secret-container  security-group  server
server-group  service  snapshot  stack  subnet  telemetry  volume  volume-type  vpc-peering  vpn
```

Output is a table by default; `-o json` prints JSON. The full guide is [docs/user-guide-cli.md](docs/user-guide-cli.md).

## Documentation

[docs/README.md](docs/README.md) is the index. Release notes, with upgrade steps for each version, are in [docs/releases/](docs/releases/) and the short version is [CHANGELOG.md](CHANGELOG.md).

## Contributing and license

See [CONTRIBUTING.md](CONTRIBUTING.md) to help. CloudX is released under the [MIT license](LICENSE).
