# Changelog

All notable changes to CloudX. Each release from 0.7.0 on has full notes, including upgrade steps, in
`docs/releases/<tag>.md`; this file is the short version. Versions before 1.0.0 may change configuration and database
schema between releases, so read the upgrade steps before moving a running cloud.

## Unreleased

### Fixed
- A second dual-stack port on the same network no longer fails with a 502. IPv6 addresses held by other ports were not
  counted, so every automatic allocation returned the same address (`::2`) and OVN refused the duplicate. An IPv6 address
  given in `fixed_ips` was never checked either: it could be a duplicate or lie outside the subnet. It now gets the same
  checks as an IPv4 address (400 outside the subnet or not an address, 409 when taken or the gateway).
- `PATCH /volume-types/{ref}` exists. `cloudx volume-type set` and the dashboard's API client already called it and got a 405. It changes
  `description`, `max_iops`, `max_throughput` and `is_public`; `backend`, `disk_type` and `lvm_type` are refused with 422.

## 1.0.0 (2026-10-28)

No code change since 1.0.0-rc1; the full list is in the rc1 section below and the notes are in `docs/releases/v1.0.0.md`.

### Documentation
- How to download and check the release assets: `docs/deb-install.md` section 3 ("Downloading a release"), the quickstart and the README.
- The tarball-to-packages upgrade runbook was fixed after a rehearsal with the rc1 assets: it installs from the downloaded
  files, keeps `cloudx-storage-init.service` (without it a loop-file volume group is gone after a reboot) and checks
  `system-id.conf` on compute nodes set up by 0.8.x.
- Troubleshooting entry for name lookups from a VM that take 5 seconds because a resolver in `dns_upstream` does not answer.
- Not tested: a cloud with the controller and the compute nodes on separate machines running different versions during the upgrade.

## 1.0.0-rc1 (2026-10-27)

The notes for the release this candidate leads to are in `docs/releases/v1.0.0.md`.

### Added
- OpenStack-style .deb packages: `cloudx-common`, `cloudx-db`, one package per API or daemon, role meta packages
  (`cloudx-controller`, `cloudx-compute`, `cloudx-storage`) and a standalone `cloudx-cli`. The twenty stateless APIs sit
  behind nginx. Built from `packaging/manifest.toml` with `make deb`; the venv is pinned with pip constraints.
- A service catalog in auth-api (`cloudx-manage catalog sync`). Services, workers, host daemons and the CLI resolve
  their peers through it instead of per-service URLs in config.
- `cloudx-manage` (`db sync`, `conf init`) and host setup scripts shipped with the role packages:
  `cloudx-setup-compute`, `cloudx-setup-ceph-secret`, `cloudx-setup-storage`.
- Messaging (Zaqar-like queues) and Reservation (Blazar-like leases) services.
- An all-in-one quickstart for the .deb packages (`docs/quickstart.md`), the full deb install guide and a lab upgrade
  runbook (`docs/deb-install.md`), and documented known limitations.
- Release assets: the `.deb` files, the tarball and `SHA256SUMS` are attached to each GitHub release. `docs/deb-install.md`
  section 3 ("Downloading a release"), the quickstart and the README show how to download and check them.

### Removed
- Files left over from the Docker Compose era and one-off scripts: the container entrypoints and reference configs under
  `services/` (`ovn`, `postgres`, `rabbitmq`, `haproxy`, `keepalived`, `backup`, `certs`, `grafana`, `prometheus`),
  `make generate-certs`, `.env.example`, the old per-service CLIs in `cli/` (`cloudx` replaces them), `scripts/bootstrap.sh`,
  `postgres-init/`, the phase 7 and 8 test scripts, the VirtualBox and PowerShell lab helpers, `docs/superpowers/` and an empty `CLAUDE.md`.
  The docs that described them say what is not provided.
- The "phase" prefix of three docs: `production-readiness.md`, `telemetry-billing-events.md`, `orchestration-vpc-bgp.md`.

### Fixed
- RBD volumes boot and attach against a real cephx cluster (Ceph 19.2.3 clients).
- Router gateways land on a real gateway chassis, and the node's OVN system-id survives an Open vSwitch restart.
- A clean .deb install now boots a VM: stale chassis cleanup, `ovn-bridge-mappings`, block storage preparation.
- Deleting a security group removes its OVN port group and ACLs; rule ports set from the CLI are kept.
- Subnets created without a nameserver no longer get the lab's resolver address (`172.20.0.118`) on an install without the
  DNS service: VMs get `dns_upstream` instead. `cloudx.conf.sample` ships `dns_public_address` and `dns_resolver_address`
  commented out.
- Node configuration files are written mode 640, owner `root:cloudx`, with the controller's credentials.
- The RabbitMQ password is no longer written to logs; RPC connections use TCP keepalive, so a dead peer is noticed in
  about a minute.
- A server deleted while it is still in BUILD no longer leaves a running domain behind; tap devices are recreated
  before a domain starts after a host reboot.
- ha-watchdog falls back to the last ha-api URL that resolved when the catalog is down.
- `cloudx ha protect` and `unprotect` accept a server name, as the CLI sends it (they only took an ID before).
- A missing load balancer API no longer stops the DNS worker's sweep of automatic names.
- `cloudx flavor list` shows memory and disk.
- A server group no longer accepts a member on a node that already holds one when every node is taken (409
  `No valid host`, not a silent placement on a used node).
- `cloudx scaling-group scale-out/scale-in` and stack calls no longer answer 401 (orchestration accepts `X-Auth-Token`).
- The telemetry worker sends metrics again: it read the wrong key of the server list and looked up libvirt domains by
  the wrong name, so no CPU, memory, disk or network data reached telemetry-api.

### Changed
- Pre-release packages get a tilde in their Debian version (`1.0.0~rc1-1`), so `apt` sorts a release candidate before
  the release.
- All documentation is English; the tarball install guide moved to `docs/tarball-install.md`; new architecture page,
  CLI guide checked by a test, and tutorials (three-node cloud, auto-scaling, Ceph, Kubernetes, dashboard tour).

## 0.8.6 - 2026-10-20
Database backup and scheduled backups, Instance HA hardening (watchdog and a second ha-api with a leader lock), an error
reaper, iSCSI cleanup and an IP allocation race fix.

## 0.8.5 - 2026-10-20
Four new services, each "not 1:1" with its OpenStack counterpart: File Store (Manila-like), Container (Zun-like),
Database (Trove-like) and Instance HA (Masakari-like).

## 0.8.0 - 2026-10-18
DNS service (Designate-like, on BIND9), client CRL for load balancer listeners.

## 0.7.21 - 2026-10-17
L7 ordering fix for the application load balancer; per-domain unique names and domain quotas in auth.

## 0.7.20 - 2026-10-17
Domain isolation in auth, L7 parity for the application load balancer, dashboard pages for stacks, alarms and role
assignments.

## 0.7.19 - 2026-10-17
Volume replication, VPC peering, VPN as a service, BGP and the K3s Kubernetes engine move from stubs to working features.

## 0.7.18 - 2026-10-11
Service-to-service calls use a shared `internal_token` instead of the admin password.

## 0.7.17 - 2026-10-10
Closes most limitations carried over from 0.7.5 to 0.7.16 (networking, compute and telemetry migrations).

## 0.7.16 - 2026-10-10
Orchestration, load balancer, telemetry with alarms, and billing.

## 0.7.13 - 2026-10-10
Core parity: configuration moves to OpenStack-style `/etc/cloudx/cloudx.conf` plus per-service files; Placement becomes
the scheduler; server groups with affinity and anti-affinity.

## 0.7.9 - 2026-10-07
Hardening: `internal_token` required on every node for `/internal/*` routes.

## 0.7.6 - 2026-10-07
`/metrics` on every API requires a bearer token. Fixes for items left open after 0.7.5.

## 0.7.5 - 2026-10-07
Telemetry, billing and events work end to end, with the dashboard pages on top of them.

## 0.7.0 - 2026-10-05
No new API features. Secrets are mandatory (no default passwords or keys), the database schema is managed by Alembic, and
volume quotas are enforced.

## 0.6.0 - 2026-10-03
Native systemd deployment replaces Docker Compose. Dashboard and CLI cover the full API.

## 0.2.5 - 2026-08-21
Production readiness and multi-node architecture.

## 0.2.0 - 2026-08-07
Third roadmap phase complete (preceded by 0.2.0-alpha-1 and 0.2.0-alpha-2 in June).

## 0.1.0 - 2026-06-13
First tagged release: single-node cloud on libvirt/QEMU/KVM and OVN, with volume types (LVM and Ceph) and a dashboard.
