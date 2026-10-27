# Architecture

CloudX is a set of small services that run as systemd units on Ubuntu 24.04. A **controller** runs the APIs and the databases, **compute nodes** run the VMs, and **storage nodes** serve volumes. On one machine all three roles are the same host. [deb-install.md](deb-install.md) says which package belongs to which role; this page explains how the pieces fit together.

## Overview

```
                      users: cloudx CLI, dashboard (:8080), your own HTTP clients
                                              |
                                              v
   +------------------------------------------ CONTROLLER ------------------------------------------+
   |  nginx  --unix sockets-->  20 REST APIs (auth, compute, networking, image, block-store, ...)    |
   |  conductor, placement-api, dashboard, metadata-api, workers (image, billing, event, k8s, ...)  |
   |                                                                                                  |
   |  PostgreSQL        RabbitMQ         memcached        OVN central (NB :6641, SB :6642, northd)    |
   +-----------+--------------+---------------------------------------------+-----------------------+
               |  AMQP: host-addressed casts and RPC                         | OVN SB protocol
               v                                                             v
   +---------- COMPUTE NODE ----------------------+            +---------- STORAGE NODE ----------+
   |  compute-worker --> libvirt / QEMU / KVM     |   iSCSI    |  block-store-worker              |
   |  ovn-controller + Open vSwitch (br-int,      | <--------> |  LVM volume group (vg-cloudx)    |
   |  br-ex), metadata-proxy, lb-worker,          |   or Ceph  |  or a Ceph RBD cluster           |
   |  vpn-worker, bgp-worker, telemetry-worker,   |   RBD      +----------------------------------+
   |  ha-watchdog                                 |
   +----------------------------------------------+
```

Gateway nodes (usually the controller or infra nodes) are OVN chassis that also hold a provider interface on `br-ex`; the routers' external ports live there.

## How the services talk

- **REST over HTTP** between APIs and to users. Every API checks a token issued by `auth-api`. Calls between services that act without a user (workers, schedulers, `/internal/*` routes) carry the shared `internal_token` from `/etc/cloudx/cloudx.conf`.
- **Service catalog.** A service finds its peers through the catalog in `auth-api` (`GET /internal/catalog`, cached for 60 seconds), not through per-service URLs in its own config. A `<name>_api_url` key in the config overrides the catalog. Only `auth_api_url` must be set on every node. The CLI reads the catalog from the login response. See [deb-install.md](deb-install.md) section 5.
- **RabbitMQ** carries asynchronous work. An API casts a message such as `build_instance` to the queue of the node that owns the resource (`compute.<host>`, `block-store.<host>`); workers consume their own queue plus the shared one. Events are published to the `cloudx.events` fanout exchange, which the event service and the dashboard consume.
- **Conductor.** The compute and block-store workers do not write to the database. They call `conductor` over RPC to update a server, volume or snapshot record.
- **PostgreSQL.** One database per area (`cloudx` for most services, plus `cloudx_orchestration`, `cloudx_events`, `cloudx_billing`, `cloudx_k8s_engine`, `cloudx_key_manager`). Each service owns its tables and migration history; `cloudx-manage db sync` applies them all.
- **OVN.** `networking-api` is the only writer of the OVN Northbound database (networks, routers, ACLs, DHCP, NAT). `ovn-northd` turns that into Southbound flows, and `ovn-controller` on each node programs Open vSwitch.

## What creates a VM

`POST /servers` on `compute-api`:

1. Validate the flavor, the key pair, the network and quotas. Choose the host: `compute-api` claims capacity from `placement-api`, which picks the provider with the most free memory and honours server groups (affinity, anti-affinity) and availability zones. If placement is down or has no inventory yet, `compute-api` falls back to the node with the fewest servers.
2. Create the root volume in `block-store-api` from the image (every VM boots from a volume; there is no ephemeral disk).
3. Create the port(s) in `networking-api`, which assigns the address and writes the logical port and its security group ACLs to OVN.
4. Store the server with status `BUILD` and return. The API does not wait for the VM.
5. Cast `build_instance` to the queue of the chosen compute node. `compute-worker` builds the config drive, defines the libvirt domain, binds the tap device to `br-int` with the port's `iface-id`, attaches the root volume through block-store and starts the VM.
6. The worker reports progress through `conductor`; the server becomes `ACTIVE`, or `ERROR` with the reason in `error_message`.

If a step fails after resources were created, `compute-api` releases the claim, deletes the ports and the auto-created root volume, and marks the server `ERROR`.

## Services

Package names are `cloudx-<service>`. APIs marked *nginx* listen on a unix socket and nginx answers on the port shown; *direct* services listen on the port themselves. Roles: **C** controller, **N** compute node, **S** storage node, **-** installed on its own where needed.

| Service | Port | Role | What it does |
|---|---|---|---|
| `auth-api` | 8005 nginx | C | Users, projects, domains, roles, access keys, tokens, the service catalog |
| `compute-api` | 8003 nginx | C | Servers, flavors, key pairs, server groups, aggregates, quotas |
| `conductor` | - | C | Database proxy and event publisher for the compute and block-store workers |
| `placement-api` | 8006 nginx | C | Capacity inventory and host selection |
| `compute-worker` | - | N | Runs VMs through libvirt; registers the node in placement |
| `networking-api` | 8001 nginx | C | Networks, subnets, ports, routers, floating IPs, security groups, VPC peering, VPN and BGP records; writes OVN |
| `metadata-api` | 8775 direct | C | Instance metadata and network data |
| `metadata-proxy` | 8776 direct | N | Serves `169.254.169.254` to VMs on its host |
| `lb-worker` | - | N | HAProxy (L7) and OVN load balancer (L4) data plane |
| `vpn-worker`, `bgp-worker` | - | N | strongSwan and BIRD speakers for VPN and BGP |
| `load-balancer-api` | 8008 nginx | C | Load balancers, listeners, pools, members, health monitors |
| `dns-api` | 8016 nginx | C | DNS zones and records |
| `dns-worker` | - | C | Writes zone files and reloads BIND (host preparation first; see deb-install.md section 8) |
| `block-store-api` | 8004 nginx | C | Volumes, volume types, snapshots, backups |
| `block-store-worker` | - | S | LVM or Ceph RBD volumes, iSCSI export, backups to object storage |
| `image-api`, `image-worker` | 8002 nginx | C | Image catalog and storage (files or MinIO) |
| `object-store-api` | 8007 direct | C | S3-style buckets and objects |
| `key-manager-api` | 8014 nginx | C | Secrets and secret containers |
| `file-store-api` | 8017 nginx | C | NFS shares |
| `file-store-worker` | - | one node | LV per share and an NFS-Ganesha server per share network (needs `nfs-ganesha`) |
| `orchestration-api`, `orchestration-worker` | 8013 nginx | C | Stacks and auto-scaling groups |
| `k8s-engine-api`, `k8s-engine-worker` | 8015 nginx | C | K3s clusters on CloudX VMs |
| `container-api` | 8018 nginx | C | Single containers with a tenant IP |
| `container-worker` | - | any node | Runs Docker containers (needs `docker.io`) |
| `database-api` | 8019 nginx | C | PostgreSQL and MariaDB instances on a VM or container |
| `messaging-api` | 8022 nginx | C | Message queues |
| `reservation-api` | 8023 nginx | C | Capacity leases |
| `ha-api` | 8020 nginx | C | Host monitor and instance recovery (a second instance is allowed) |
| `ha-watchdog` | 8021 | N | Self-fencing of protected VMs when a node is isolated |
| `telemetry-api`, `telemetry-worker` | 8010 nginx | C, N | Metrics, alarms, rollups |
| `billing-api`, `billing-worker` | 8011 nginx | C | Rating sheets, usage records, invoices |
| `event-api`, `event-worker` | 8012 nginx | C | Event log and webhook subscriptions |
| `reaper` | - | C | Cleans up failed builds and runs scheduled database backups |
| `dashboard` | 8080 direct | C | The web UI |

## Other ports

| Port | Used by |
|---|---|
| 5432 | PostgreSQL |
| 5672 | RabbitMQ (AMQP) |
| 11211 | memcached |
| 6641, 6642 | OVN Northbound and Southbound databases (on the controller) |
| 6081/udp | Geneve tunnels between nodes (OVN default) |
| 3260 | iSCSI portal on an LVM storage node |
| 53 | DNS: the authoritative and resolver BIND instances on two separate addresses |
| 2049 | NFS from the file-store worker |

## State and failure

- **Everything durable is in PostgreSQL**, plus the files each node keeps: images and volumes under `/var/lib/cloudx`, the libvirt domains, and OVN's own databases on the controller. Back up PostgreSQL and `/etc/cloudx` (it holds the secrets).
- **Running VMs do not depend on the control plane.** QEMU processes live under `machine.slice`, not in a CloudX unit, so restarting or upgrading CloudX services leaves them running. This was checked on the lab during the package upgrade (qemu PIDs unchanged).
- **Single points.** PostgreSQL, RabbitMQ and OVN central are one instance each and are not managed for HA by the packages. With a second `ha-api` and the watchdog, protected VMs are recovered when a compute node dies; if the controller itself dies, the data plane keeps running but nothing can be created, changed or evacuated. See [production-readiness.md](production-readiness.md) for what exists around backups, monitoring and HA.
- **No TLS between services** and none on the nginx front yet; keep the management network private.
