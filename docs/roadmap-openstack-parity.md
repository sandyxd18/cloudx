# 🗺️ Roadmap to CloudX Base Services Parity

> **This document** analyses the current state of **CloudX** and details the work needed
> to reach higher similarity with the seven CloudX base services:
> **Compute, Networking, Block Storage, Image, Identity, Placement, and the CloudX Dashboard**.
>
> The analysis is based on the actual code in `services/`, the documentation in `docs/feature-comparison.md`,
> and a comparison with the behaviour of the real CloudX.
>
> **Initial score (June 2026):**
> Identity ~65% · Compute ~80% · Networking ~65% · Image ~70% · Block Storage ~75% · Placement ~70% · CloudX Dashboard ~55%
>
> **✅ Score after Phases 1–4 (end of August 2026):**
> Identity ~93% · Compute ~94% · Networking ~88% · Image ~90% · Block Storage ~93% · Placement ~92% · CloudX Dashboard ~85%

---

## 📋 Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Identity — auth-api](#2-identity--auth-api-currently-65)
3. [Compute — compute-api + compute-worker](#3-compute--compute-api--compute-worker-currently-80)
4. [Networking — networking-api](#4-networking--networking-api-currently-65)
5. [Image — image-api](#5-image--image-api-currently-70)
6. [Block Storage — block-store-api + block-store-worker](#6-block-storage--block-store-api--block-store-worker-currently-75)
7. [Placement — placement-api](#7-placement--placement-api-currently-70)
8. [CloudX Dashboard — dashboard](#8-cloudx-dashboard--dashboard-currently-55)
9. [Cross-Cutting Infrastructure](#9-cross-cutting-infrastructure)
10. [Priorities and Effort Estimates](#10-priorities-and-effort-estimates)
11. [Target Architecture](#11-target-architecture)

---

## 1. Executive Summary

CloudX is a Docker Compose-based private cloud implementation that is already very mature for
a single-node lab context. The current implementation already uses **real** backends:
libvirt/QEMU/KVM, OVN/OVS, LVM, Ceph RBD, MinIO — not mocks or simulators.

### Current State vs Target

| Service | Initial | After P1–P4 | Target | Remaining Gap (Phase 5+) |
|---|:---:|:---:|:---:|---|
| Identity (`auth-api`) | ~65% | **~93%** | ~95% | High Availability, Fernet token |
| Compute (`compute-api`) | ~80% | **~94%** | ~95% | Live Migration (multi-node), HA |
| Networking (`networking-api`) | ~65% | **~88%** | ~90% | BGP Dynamic Routing, FWaaS |
| Image (`image-api`) | ~70% | **~90%** | ~92% | Swift backend, Image encryption |
| Block Storage (`block-store-api`) | ~75% | **~93%** | ~95% | Volume Replication, HA |
| Placement (`placement-api`) | ~70% | **~92%** | ~93% | Full scheduler integration (multi-node) |
| CloudX Dashboard (`dashboard`) | ~55% | **~85%** | ~90% | Heat UI, Octavia UI |

### What Is Already Very Good (No Changes Needed)

- ✅ RabbitMQ AMQP-based async operations (similar to oslo.messaging)
- ✅ Conductor pattern (workers do not access the DB directly)
- ✅ Real VMs via libvirt/QEMU/KVM with boot-from-volume
- ✅ Full OVN/OVS integration with floating IPs and security group ACLs
- ✅ LVM + Ceph dual-backend storage
- ✅ WebSocket live events to the dashboard
- ✅ JWT auth with project scoping
- ✅ Metadata API (EC2 + CloudX format, ~92%)
- ✅ A functional `cloudx` CLI

---

## Fase 1: milestone core service (v0.7.10 - v0.7.13)

> **Catatan:** Commit, push, dan release hanya dilakukan **sekali** pada versi **v0.7.13**. Milestone **v0.7.10** hingga **v0.7.12** merupakan milestone internal tanpa git tag.

| Versi | Sub-fase / Area | Deskripsi Perubahan | Status |
|---|---|---|:---:|
| **v0.7.10** | 1a Keamanan & Isolasi | • Scheduler Placement + role reader + config `.conf` (SELESAI)<br>• `project_id` & ownership di Neutron (Router, Subnet, Port, FloatingIP, PortForwarding, SG rule; nama unik per project)<br>• Role table + role assignments Keystone<br>• Token access-key memakai `AccessKey.project_id`<br>• Fix rule SG IPv6 (ACL `ovn_cli` hanya ip4) | SELESAI |
| **v0.7.11** | 1b Data Plane | • Neutron `allowed_address_pairs`, router update + extra routes, MTU, quota<br>• Cinder `revert-to-snapshot` & `retype`<br>• Glance status machine (`queued`/`saving`/`active`), inspeksi `qemu-img`, properties kustom, `os_hash_algo`/`os_hash_value` | SELESAI (diuji di VM) |
| **v0.7.12** | 1c Operasional Nova | • Flavor `extra_specs`, aggregates/AZ, `/limits` & `quota-sets`<br>• Instance actions, `os-services`, cold migrate & evacuate | SELESAI (diuji di VM) |
| **v0.7.13** | 1d Conformance | • Script e2e per service + dokumen parity<br>• Commit, push, tag, dan release | Release Tag |

> **Catatan:** Gap di luar Fase 1 tetap dialokasikan pada Fase 2/3 (orchestration, LB L7, alarms, K8s, VPNaaS, dsb).

## Fase 2: milestone advanced (v0.7.14 - v0.7.16)

> **Catatan:** Commit, push, dan release hanya dilakukan **sekali** pada **v0.7.16**. **v0.7.14** dan **v0.7.15** adalah milestone internal tanpa git tag. Fase 3 menyusul setelahnya.

| Versi | Sub-fase / Area | Deskripsi Perubahan | Status |
|---|---|---|:---:|
| **v0.7.14** | 2a Orchestration (Heat-like) | • Saat ini hanya ada Auto-Scaling Group; **engine stack (Heat) dibangun dari nol** (tabel `stacks`, `stack_resources`, `stack_events`)<br>• Perbaiki bug ASG: `scale-out` manual hanya membuat record dengan `server_id` palsu, tanpa membuat VM<br>• Template: parameters, outputs, `get_param`/`get_attr`/`get_resource`<br>• Resource: Server, Volume, Network, Subnet, Router, FloatingIP, SecurityGroup<br>• Dependency graph, update, rollback, suspend/resume, preview<br>• Tidak termasuk: autoscaling group, nested stack | Rencana |
| **v0.7.15** | 2b Load Balancer | • Data plane ganda: **OVN LB untuk L4**, **HAProxy untuk L7**<br>• Listener, pool, member, health monitor; algoritma round-robin / least-connections / source-ip<br>• L7 policy & rule (path/host) via HAProxy<br>• VIP lewat port Neutron / floating IP<br> **Status:** ALB (HAProxy di netns compute-node via `lb-worker`, L7 policy path, health check) terbukti dengan trafik nyata. NLB (OVN) juga terbukti: klien di jaringan lain lewat router terdistribusi ke 2 backend (driver memasang LB ke switch VIP, router, dan switch non-gateway router, seperti ovn-octavia-provider). Klien di L2 yang sama dengan VIP belum bisa (hairpin tanpa SNAT). Round-robin/least-connections sejati hanya di ALB | Selesai |
| **v0.7.16** | 2c Telemetry, Alarms, Billing | • Alarm berbasis threshold + aksi webhook/event (exchange `cloudx.events`)<br>• Retensi & agregasi metrik<br>• Tarif billing per resource dari konfigurasi (termasuk tarif LB)<br>• Commit, push, tag, dan release<br> **Status:** alarm (`/telemetry/alarms`, event `telemetry.alarm.state_changed` ke `cloudx.events` → webhook subscription), rollup per jam (`/telemetry/rollups`), tarif seed via `billing_rate_<type>` terbukti di VM | Selesai |

---

## 2. Identity — `auth-api` (Currently: ~65%)

### Current State of the Code

Main file: `services/auth/app/main.py`

Already present: JWT tokens, project/user/group CRUD, role assignment (admin/member/reader),
AWS-style access keys, automatic admin bootstrap.

### Gaps to Address

#### 🔴 High Priority

**1. Service Catalog**

CloudX Identity has endpoint discovery — after login, the client receives
the list of URLs of all services per region. This is needed so that standard CloudX SDK/CLI
clients can auto-discover endpoints.

```
Endpoints to add:
GET  /v3/services              — list registered services
POST /v3/services              — register a service
GET  /v3/endpoints             — list endpoints per service
POST /v3/endpoints             — register an endpoint (internal/public/admin)
GET  /v3/auth/catalog          — embedded in the token response
```

**Work:**
- Add `services` and `endpoints` tables to the DB
- Add a `catalog` field to the `POST /auth/tokens` response
- Bootstrap: auto-register all cloudx services in the catalog at startup
- The token response format must match Identity v3: `token.catalog[]`

---

**2. Multi-Scope Token (Domain & System Scope)**

CloudX supports tokens with these scopes: `project`, `domain`, `system`.
Currently cloudx only supports the `project` scope.

```python
# Standard Identity v3 request body:
{
  "auth": {
    "identity": { ... },
    "scope": {
      "project": {"id": "..."},   # already present
      # NOT YET PRESENT:
      "domain": {"id": "..."},
      "system": {"all": true}
    }
  }
}
```

**Work:**
- Extend the JWT payload with a `scope_type` field (project/domain/system)
- Add scope validation in `auth_middleware.py`
- Domain scope is needed for cross-project admin operations

---

**3. oslo.policy-style RBAC** ✅ (Done in Phase 3)

Currently role checks are done manually (`if token.role != "admin"`).
The real CloudX uses a `policy.yaml` file that can be configured per rule.

```yaml
# Example policy.yaml (oslo.policy format)
"identity:create_user": "rule:admin_required"
"identity:list_users": "rule:admin_or_owner"
"identity:get_project": ""  # any authenticated user
```

**Work:**
- Implement a simple policy engine that reads from `policy.yaml`
- Replace hardcoded `is_admin` checks with `policy.enforce(rule, target, credentials)`
- Each service needs its own `policy.yaml` file

---

#### 🟡 Medium Priority

**4. Domain Management**

```
POST /v3/domains          — create a domain
GET  /v3/domains          — list domains
PATCH /v3/domains/{id}    — update a domain
DELETE /v3/domains/{id}   — delete a domain
```

CloudX supports the hierarchy: Domain → Project → User.
Currently cloudx is flat (one implicit domain).

**Work:**
- Add a `domains` table to the DB
- Extend the `projects` and `users` tables with a `domain_id` FK
- Implement the domain CRUD endpoints
- A `default` domain is created at bootstrap

---

**5. Application Credentials**

Similar to the existing access keys, but in the Identity v3 format:
- Bound to a specific user (not project-global)
- Can have limited `roles`
- The secret cannot be retrieved again after creation

```
POST /v3/users/{user_id}/application_credentials
GET  /v3/users/{user_id}/application_credentials
DELETE /v3/users/{user_id}/application_credentials/{id}
```

---

#### 🟢 Low Priority / Out of Lab Scope

- **Federation / SAML / OAuth2** — Complex, outside the lab scope
- **Multi-factor Authentication** — Not relevant for a local lab
- **Fernet Token** — JWT is functional enough
- **Trust (delegated token)** — Advanced, outside scope

### Identity Effort Estimate

| Work | Complexity | Effort |
|---|:---:|---|
| Service Catalog | Medium | 2–3 days |
| Multi-scope token | Low | 1 day |
| oslo.policy RBAC | High | 3–5 days |
| Domain management | Medium | 2–3 days |
| Application credentials | Low | 1 day |

**Target after everything is done: ~90%**

---

## 3. Compute — `compute-api` + `compute-worker` (Currently: ~80%)

### Current State of the Code

Main file: `services/compute/app/main.py` (~49KB)
Worker: `services/compute_worker/app/main.py`

This is the most mature service. Already present: the full server lifecycle, pause/resume,
suspend/unsuspend, resize, rebuild, rescue, server groups, keypair, flavor,
multi-NIC, volume attach/detach, console log, password injection.

### Gaps to Address

#### 🔴 High Priority

**1. noVNC / SPICE Console Proxy**

This is one of the most visible missing features. The libvirt VNC port already exists
(visible in the `NOVNC_BASE_URL` env var), but there is no proxy to the browser yet.

```
noVNC flow:
Browser -> noVNC JS client -> WebSocket proxy -> libvirt VNC port

Components to add:
- websockify container (or a native Python implementation)
- Endpoint: GET /servers/{ref}/console-url -> {type: "novnc", url: "..."}
- noVNC HTML UI embedded in the dashboard
```

**Work in compute-worker:**
```python
# Add an RPC method:
def get_vnc_port(server_id) -> int:
    domain = conn.lookupByName(vm_name)
    xml = ET.fromstring(domain.XMLDesc())
    port = xml.find(".//graphics[@type='vnc']").get("port")
    return int(port)
```

**Infrastructure work:**
- Add a `novnc-proxy` container to `docker-compose.yml` (image: websockify)
- Port mapping configuration: host `6080` → libvirt VNC ports

---

**2. Resize Confirm / Revert**

Currently resize is executed immediately (live vCPU + RAM hotplug).
The real CloudX has two steps: VERIFY_RESIZE → confirm or revert.

```
CloudX Compute resize flow:
POST /servers/{id}/resize         -> status: VERIFY_RESIZE
POST /servers/{id}/confirmResize  -> the size changes permanently
POST /servers/{id}/revertResize   -> roll back to the old flavor
```

**Work:**
- Save an `original_flavor_snapshot` when the resize starts
- Server status: `RESIZE` → `VERIFY_RESIZE`
- New endpoints: `POST /servers/{id}/confirmResize` and `/revertResize`
- Worker: hotplug vCPU/RAM on `confirm`, roll back on `revert`

---

**3. Shelve / Unshelve**

Shelve is "hibernate to object storage" — the VM is snapshotted to an image,
then the instance is stopped and its resources (vCPU, RAM) are released.
Unlike suspend, which saves the state to disk but keeps using resources.

```
POST /servers/{id}/shelve
  -> create a snapshot image of the current VM
  -> stop the VM
  -> delete the libvirt domain (release resources)
  -> status: SHELVED_OFFLOADED

POST /servers/{id}/unshelve
  -> create a new VM from the snapshot image
  -> delete the temporary snapshot image
  -> status: ACTIVE
```

**Work:**
- Endpoints `POST /servers/{id}/shelve` and `/unshelve`
- The worker must be able to create snapshots and restore from a snapshot
- Needs integration with image-api to store the shelved image

---

**4. Lock / Unlock Server**

A simple feature that prevents accidental modification.

```python
POST /servers/{id}/lock    # mark the server as locked
POST /servers/{id}/unlock  # release the lock

# Every modifying operation must check:
if server.locked and not is_admin:
    raise HTTPException(403, "Server is locked")
```

**Work:**
- Add `locked` (boolean) and `locked_by` columns to the `servers` table
- Endpoints `POST /servers/{id}/lock` and `POST /servers/{id}/unlock`
- Guard all action endpoints with a lock status check

---

**5. Server Tags**

Simple tagging for servers.

```
GET    /servers/{id}/tags        — list tags
PUT    /servers/{id}/tags        — replace all tags
PUT    /servers/{id}/tags/{tag}  — add a tag
DELETE /servers/{id}/tags/{tag}  — delete a tag
```

**Work:**
- Add a `server_tags` table (server_id, tag) to the DB
- Implement the 4 endpoints above
- Add a `?tags=` filter to `GET /servers`

---

#### 🟡 Medium Priority

**6. Aggregates & Availability Zones**

Needed if multi-node simulation is wanted in the future.

```
POST /os-aggregates              — create an aggregate
GET  /os-aggregates              — list aggregates
POST /os-aggregates/{id}/action  — add/remove a host
```

**Work:**
- Add `aggregates` and `aggregate_hosts` tables
- Availability zone derived automatically from the aggregate
- Default AZ: `compute`

---

#### 🟢 Low Priority

- **Ephemeral Disk** — Deliberately excluded (volume-first architecture)
- **Live Migration** — Single-node, not relevant
- **vfat config drive** — ISO9660 is enough
- **Trigger Crash Dump** — Not relevant for the lab

### Compute Effort Estimate

| Work | Complexity | Effort |
|---|:---:|---|
| noVNC proxy | High | 3–5 days |
| Resize confirm/revert | Medium | 2 days |
| Shelve/unshelve | Medium | 2–3 days |
| Lock/unlock | Low | 0.5 day |
| Server tags | Low | 1 day |
| Aggregates/AZ | Medium | 2–3 days |

**Target after completion: ~92%**

---

## 4. Networking — `networking-api` (Currently: ~65%)

### Current State of the Code

Main file: `services/networking/app/main.py` (~71KB — the largest in the project)

Already present: network, subnet, router, floating IP, security group, port, full OVN integration,
provider network via localnet, ARP proxy.

### Gaps to Address

#### 🔴 High Priority

**1. QoS (Quality of Service)**

CloudX Networking supports per-port bandwidth limiting via a QoS policy.
OVN already supports this mechanism through the `QoS` table in the NB DB.

```
Endpoints to add:
POST /qos/policies                              — create a QoS policy
GET  /qos/policies                              — list
PATCH /qos/policies/{id}                        — update
DELETE /qos/policies/{id}                       — delete
POST /qos/policies/{id}/bandwidth_limit_rules   — add a rule

# Apply to a port:
PATCH /ports/{id}  {"qos_policy_id": "..."}
```

**Work in OVN:**
```python
# OVN NB DB: set a QoS rule
ovn_nbctl("qos-add", ls_name, "from-lport", "100",
          f"inport == {port_id}", "rate=10000", "burst=20000")
```

---

**2. IPv6 Support** ✅ (Done in Phase 3)

Currently IPv4 only. CloudX supports dual-stack and IPv6-only subnets.

```python
# Subnet create with IPv6:
POST /subnets {
  "ip_version": 6,
  "cidr": "fd00::/64",
  "ipv6_ra_mode": "slaac",
  "ipv6_address_mode": "slaac"
}
```

**Work:**
- Extend IPAM to support IPv6 CIDRs
- OVN DHCPv6 options: `SET_DHCPV6_STATEFUL_OPT` or SLAAC via RA
- Validate `ip_version` 4 vs 6 in all subnet endpoints
- Dual-stack port allocation (one port with IPv4 + IPv6)

---

**3. Shared Networks (Cross-Project)**

CloudX supports networks that can be shared between projects.
Useful for provider networks that every project accesses.

```python
# Create a shared network:
POST /networks {"shared": true}
# Policy: a shared network is visible to all projects on GET /networks
```

**Work:**
- Add a `shared` column to the `networks` table
- The `GET /networks` query must return networks owned by the project OR `shared=true`
- Validate access when creating a port on a shared network

---

**4. Subnet Pools**

A pool of subnets that can be allocated from a large prefix.

```
POST /subnetpools      — create a pool (e.g. 10.0.0.0/8)
GET  /subnetpools      — list pools

# When creating a subnet:
POST /subnets {"subnetpool_id": "...", "prefixlen": 24}
# -> auto-allocate 10.x.y.0/24 from the pool
```

**Work:**
- New table: `subnet_pools` (prefix, min_prefixlen, max_prefixlen, default_prefixlen)
- An IPAM allocator that draws from the pool automatically
- Validate overlap between allocations

---

**5. Port Forwarding (per-port DNAT)** ✅ (Done in Phase 3)

Different from a floating IP (which maps one public IP to one VM).
Port forwarding maps `public_ip:port` → `vm_ip:port`.

```
POST /floatingips/{id}/port_forwardings {
  "protocol": "tcp",
  "external_port": 8080,
  "internal_port": 80,
  "internal_ip_address": "192.168.1.10"
}
```

**Work in OVN:**
```python
# DNAT rule for port forwarding
ovn_nbctl("lr-nat-add", router_name, "dnat",
          f"{fip}:{ext_port}", f"{internal_ip}:{int_port}",
          protocol="tcp")
```

---

#### 🟡 Medium Priority

**6. Trunk Ports (VLAN-in-VLAN)**

Needed for NFV and nested VM scenarios.

```
POST /trunks {
  "port_id": "parent_port_id",
  "sub_ports": [
    {"port_id": "...", "segmentation_type": "vlan", "segmentation_id": 100}
  ]
}
```

OVN already supports trunks via `parent_port`; the API only needs to be wrapped.

---

**7. DNS Integration (Without Designate)**

A simple internal DNS implementation without the full Designate:

**Minimal work:**
- Add a `dns_domain` column to network
- Add a `dns_name` column to port
- OVN supports DNS via the `DNS` table in the NB DB

---

#### 🟢 Low Priority

- **Address Scopes** — Advanced routing, outside the lab scope
- **BGP Dynamic Routing** — Out of scope
- **VPN as a Service** — Out of scope
- **FWaaS** — Security groups are enough
- **Load Balancer (Octavia)** — Very high complexity

### Networking Effort Estimate

| Work | Complexity | Effort |
|---|:---:|---|
| QoS policies | Medium | 2–3 days |
| IPv6 support | High | 4–6 days |
| Shared networks | Low | 1 day |
| Subnet pools | Medium | 2–3 days |
| Port forwarding | Medium | 2 days |
| Trunk ports | High | 3–4 days |
| DNS integration | Low | 1–2 days |

**Target after completion: ~82%**

---

## 5. Image — `image-api` (Currently: ~70%)

### Current State of the Code

Main file: `services/image/app/main.py` (~15KB)

Already present: upload/download, list/show/delete, deactivate/reactivate,
public/private visibility, file + MinIO backends.

### Gaps to Address

#### 🔴 High Priority

**1. Image Members (Sharing Between Projects)**

Images support explicitly sharing a private image with other projects.

```
POST /images/{id}/members {"member": "target_project_id"}  -> status: "pending"
PUT  /images/{id}/members/{member_id} {"status": "accepted"}
GET  /images/{id}/members     — list members
DELETE /images/{id}/members/{id}
```

**Work:**
- New table: `image_members` (image_id, member_id, status)
- Image member CRUD endpoints
- The `GET /images` query must include images accepted by the current project
- Status workflow: pending → accepted/rejected

---

**2. Image Tags**

Simple metadata in the form of a list of strings.

```python
POST /images/{id}/tags/{tag}    — add a tag
DELETE /images/{id}/tags/{tag}  — remove a tag
GET /images?tag=linux           — filter by tag
```

**Work:**
- Add a `tags` column (JSON array) to `images`
- Endpoints `POST/DELETE /images/{id}/tags/{tag}`
- A `?tag=` filter on the list endpoint

---

**3. Task-based Import (Async from a URL)**

Currently upload is synchronous. The real Image service has a task API for async import from a URL.

```
POST /v2/tasks {
  "type": "import",
  "input": {
    "import_from": "http://cloud.centos.org/image.qcow2",
    "import_from_format": "qcow2",
    "image_properties": {"name": "centos", "disk_format": "qcow2"}
  }
}
-> {"id": "task-uuid"}

GET /v2/tasks/{id}  -> {"status": "pending"|"processing"|"success"|"failure"}
```

**Work:**
- New table: `image_tasks`
- Background worker using the existing RabbitMQ cast pattern
- Endpoints `POST /v2/tasks` and `GET /v2/tasks/{id}`

---

**4. Pagination & Advanced Filtering**

Currently filters are basic (name, visibility).

```
GET /images?sort=name:asc&limit=20&marker=<last_id>
GET /images?status=active&disk_format=qcow2&min_disk=10&max_disk=100
```

**Work:**
- Add `limit`, `marker`, `sort` parameters to `GET /images`
- Add filters: `status`, `disk_format`, `container_format`, `min_disk`, `max_disk`, `min_ram`

---

**5. Image Caching (at the image-api layer)**

Currently caching only exists in compute-worker.

**Work:**
- Implement a cache layer with a TTL in image-api
- Cache metadata in Redis (optional)

---

#### 🟡 Medium Priority

**6. Image Signing & Verification**

A simple version without the full Barbican:
- Sign the image hash with a private key
- Store the signature in the image properties
- Verify when compute-worker downloads the image

---

#### 🟢 Low Priority

- **Multi-store backend (Ceph, Swift, HTTP)** — File + MinIO is enough
- **Image encryption** — Requires Barbican, out of scope

### Image Effort Estimate

| Work | Complexity | Effort |
|---|:---:|---|
| Image members | Medium | 2 days |
| Image tags | Low | 0.5 day |
| Task-based import | Medium | 2–3 days |
| Pagination & filtering | Low | 1 day |
| Image caching | Medium | 1–2 days |
| Image signing | High | 3 days |

**Target after completion: ~88%**

---

## 6. Block Storage — `block-store-api` + `block-store-worker` (Currently: ~75%)

### Current State of the Code

API file: `services/block_store/app/main.py` (~36KB)
Worker: `services/block_store_worker/`
Drivers: `services/block_store_worker/app/drivers/`

Already present: volume CRUD, attach/detach, extend, snapshot, backup, volume types,
LVM + Ceph dual backend, boot-from-volume, transfer.

### Gaps to Address

#### 🔴 High Priority

**1. Quota Management per Project**

Block Storage supports quotas (max volumes, max snapshots, max GB) per project.
Currently there are no limits at all.

```
GET  /os-quota-sets/{project_id}         — show the quota
PUT  /os-quota-sets/{project_id}         — set the quota (admin)
DELETE /os-quota-sets/{project_id}       — reset to the default
GET  /os-quota-sets/{project_id}/detail  — usage + limit

# Default quotas (Block Storage defaults):
volumes: 10
snapshots: 10
gigabytes: 1000
```

**Work:**
- New table: `volume_quotas` (project_id, resource, hard_limit)
- New table: `quota_usages` (project_id, resource, in_use, reserved)
- Middleware that checks the quota before creating a volume/snapshot
- Quota set CRUD endpoints

---

**2. LVM Thin Provisioning** ✅ (Done in Phase 3)

Currently only thick LVM (full allocation up front).
Thin provisioning saves space — space is allocated only when data is written.

```bash
# Currently (thick):
lvcreate -L 10G -n volume-id vg-cloudx

# Thin provisioning:
# 1. Create a thin pool (once):
lvcreate -L 100G --thinpool thinpool vg-cloudx
# 2. Create a thin LV from the pool:
lvcreate -V 10G --thin -n volume-id vg-cloudx/thinpool
# Benefit: a 100G pool can hold 200G+ of thin volumes (over-provisioning)
```

**Work:**
- Add a `lvm_type: thin|thick` option to the volume type
- LVM driver: implement thin pool management
- Detect and initialise the thin pool at startup

---

**3. Volume Quota Enforcement with Reserved State**

While a volume is being created (status `creating`), the quota must already be reserved
so there is no race condition.

```python
# The correct flow:
def create_volume(project_id, size):
    if usage + size > hard_limit:
        raise QuotaExceeded()
    reserve_quota(project_id, "gigabytes", size)  # atomic reserve
    rpc.cast("block-store-worker", "create_volume", {...})
    # Worker: commit or roll back the reservation when done/on error
```

---

**4. Multi-Attach Volume** ✅ (Done in Phase 3)

Block Storage supports a volume attached to more than one server at the same time
(only for volume types that support multi-attach, usually with Ceph).

```python
POST /volumes {"multiattach": true, ...}

# Attach to a second server even though it is already attached to the first
POST /volumes/{id}/attach {"server_id": "server2_id", "mountpoint": "/dev/vdb"}
```

**Work:**
- Add a `multiattach` column to the volumes table
- Change the attach constraint: if `multiattach=true`, allow more than one attachment
- Ceph RBD already supports multi-reader

---

**5. QoS Enforcement (cgroups / blkio)**

Currently `max_iops` and `max_throughput` in the volume type are informational only.
The real Block Storage enforces them through the storage backend.

```bash
# Ceph RBD throttle:
rbd config image set pool/image-id rbd_qos_iops_limit 1000
rbd config image set pool/image-id rbd_qos_bps_limit 104857600

# LVM blkio throttle via cgroupv2:
# /sys/fs/cgroup/blkio/blkio.throttle.read_iops_device
```

**Work:**
- Ceph driver: set `rbd_qos_iops_limit` and `rbd_qos_bps_limit` via `rbd config`
- LVM driver: implement blkio rate limiting on attach

---

#### 🟡 Medium Priority

**6. Volume Migration (between backends)**

Move a volume from LVM to Ceph or the other way round.

```
POST /volumes/{id}/action {"os-migrate_volume": {"host": "ceph-backend"}}
```

**Work:**
- `qemu-img convert` between an LVM block device and Ceph RBD
- Atomic swap: delete the old volume after the copy finishes

---

**7. Consistency Groups**

A group of volumes whose snapshots are taken atomically together.
Useful for database backups.

```
POST /consistencygroups   — create a group
POST /cgsnapshots         — snapshot all volumes in the group at once
```

---

#### 🟢 Low Priority

- **Volume Encryption** — Requires Barbican
- **Volume Replication** — Needed for HA, outside the single-node scope
- **iSCSI / NFS / FC backend** — LVM + Ceph only is enough for the lab

### Block Storage Effort Estimate

| Work | Complexity | Effort |
|---|:---:|---|
| Quota management | Medium | 2–3 days |
| LVM thin provisioning | High | 3–4 days |
| Quota enforcement (reserved) | Medium | 1–2 days |
| Multi-attach volume | Medium | 2 days |
| QoS enforcement | High | 3–5 days |
| Volume migration | Medium | 2–3 days |

**Target after completion: ~88%**

---

## 7. Placement — `placement-api` (Currently: ~70%)

### Current State of the Code

Main file: `services/placement/app/main.py` (~16KB)

Already present: resource providers, inventory (VCPU/MEMORY_MB/DISK_GB),
traits, allocations, usages.

### Gaps to Address

#### 🔴 High Priority

**1. Allocation Candidates**

This is the most important missing feature — the Compute scheduler uses it
to find hosts that meet the requirements.

```
GET /allocation_candidates?resources=VCPU:2,MEMORY_MB:2048,DISK_GB:20

Response:
{
  "allocation_requests": [
    {
      "allocations": {
        "provider-uuid": {
          "resources": {"VCPU": 2, "MEMORY_MB": 2048, "DISK_GB": 20}
        }
      }
    }
  ],
  "provider_summaries": {
    "provider-uuid": {
      "resources": {
        "VCPU": {"capacity": 32, "used": 16}
      }
    }
  }
}
```

**Work:**
- Query: find providers whose capacity >= the requested resources
- Compute available = `total * allocation_ratio - reserved - used`
- Return a filtered list with summaries
- Filter by traits (required/forbidden)

---

**2. Nested Resource Providers** ✅ (Done in Phase 3)

Provider hierarchy: Node → NUMA node → CPU set.
Needed for NUMA-aware scheduling and SR-IOV.

```
Resource Provider Tree:
compute-host (root)
├── NUMA node 0
│   └── CPU 0-7
└── NUMA node 1
    └── CPU 8-15
```

**Work:**
- Add a `parent_provider_uuid` column to `resource_providers`
- API: `GET /resource_providers?in_tree={root_uuid}`
- `GET /resource_providers/{uuid}/aggregates`

---

**3. API Microversions**

CloudX Placement uses microversioning to evolve the API
without breaking changes.

```
# Request header:
CloudX-API-Version: placement 1.36

# Response header:
CloudX-API-Version: placement 1.36
x-openstack-request-id: req-...
```

**Work:**
- Middleware that parses `CloudX-API-Version: placement X.Y`
- Mapping from version to feature flags
- An `x-openstack-request-id` header on all responses

---

**4. Reshaper (Atomic Inventory + Allocation Update)**

For updating inventory and allocations atomically.
Needed during resize or live migration.

```
POST /reshaper {
  "inventories": {"provider-uuid": {"resource_class": {...}}},
  "allocations": {"consumer-uuid": {"allocations": {...}}}
}
```

**Work:**
- Endpoint `POST /reshaper`
- Wrapped in a single database transaction

---

**5. Custom Resource Classes**

Besides VCPU/MEMORY_MB/DISK_GB, CloudX supports custom resource classes
(FPGA, GPU, VGPU, SRIOV_NET_VF, and so on).

```
PUT /resource_classes/CUSTOM_GPU_A100  — create a custom resource class
GET /resource_classes                  — list all (standard + custom)
```

---

#### 🟡 Medium Priority

**6. Aggregate Association**

```
GET /resource_providers/{uuid}/aggregates
PUT /resource_providers/{uuid}/aggregates
```

**7. Forbidden Traits Filter**

```
GET /allocation_candidates?required=COMPUTE_VOLUME_EXTEND
    &forbidden=COMPUTE_NET_ATTACH_REQUIRES_PORT_FORWARDING
```

---

#### 🟢 Low Priority

- **Full scheduler integration** — Only relevant in multi-node

### Placement Effort Estimate

| Work | Complexity | Effort |
|---|:---:|---|
| Allocation candidates | Medium | 2–3 days |
| Nested resource providers | High | 3–4 days |
| API microversions | Low | 1 day |
| Reshaper | Medium | 1–2 days |
| Custom resource classes | Low | 1 day |
| Aggregate association | Low | 1 day |

**Target after completion: ~88%**

---

## 8. CloudX Dashboard — `dashboard` (Currently: ~55%)

### Current State of the Code

Frontend: `services/dashboard/frontend/src/`

Existing pages: Admin, Flavors, FloatingIPs, Images, Keypairs, Login,
Networks, Overview, Routers, SecurityGroups, ServerGroups, Servers,
Snapshots, VolumeTypes, Volumes

Stack: React + Vite + premium dark theme

### Gaps to Address

#### 🔴 High Priority

**1. noVNC Console (In-Browser)**

The most requested feature of a web dashboard. Requires the noVNC proxy (covered in the Compute section).

```jsx
// In Servers.jsx, when the "Console" button is clicked:
const ConsoleModal = ({ server }) => {
  const consoleUrl = await api.getConsoleUrl(server.id, "novnc");
  return (
    <iframe
      src={`/noVNC/vnc.html?path=websockify&host=...&port=...`}
      style={{ width: "100%", height: "600px" }}
    />
  );
};
```

**Work:**
- Integrate the noVNC JS library into the frontend
- A new modal/page for the console view
- Token-based security for the WebSocket connection

---

**2. Live Log Streaming**

Currently the log is a snapshot (fetched once). It needs auto-refresh.

```jsx
const LogViewer = ({ serverId }) => {
  useEffect(() => {
    const interval = setInterval(() => {
      api.getServerLogs(serverId).then(setLogs);
    }, 2000);
    return () => clearInterval(interval);
  }, [serverId]);
};
```

**Work:**
- A `LogViewer` component with a 2-second auto-refresh
- Use the existing WebSocket to push updates when the status changes
- Automatic scroll-to-bottom

---

**3. Network Topology View**

Graph visualisation: Server ↔ Port ↔ Network ↔ Router ↔ Floating IP.

```jsx
import ReactFlow from 'reactflow';

const TopologyPage = () => {
  const { nodes, edges } = buildTopologyGraph(networks, routers, servers);
  return <ReactFlow nodes={nodes} edges={edges} />;
};
```

**Work:**
- Install a library: `react-flow-renderer` or `cytoscape.js`
- New page: `Topology.jsx`
- Build the graph from API data (networks, routers, servers, floating IPs)
- Click a node → show a detail panel

---

**4. Image Upload via UI (Drag & Drop)**

Currently there is no image upload from the dashboard.

```jsx
const ImageUploadModal = () => {
  const onDrop = useCallback((acceptedFiles) => {
    const formData = new FormData();
    formData.append("file", acceptedFiles[0]);
    api.uploadImage(formData, onProgress);
  }, []);

  return (
    <div {...getRootProps()}>
      <ProgressBar progress={uploadProgress} />
    </div>
  );
};
```

**Work:**
- Use `react-dropzone` for drag & drop
- An upload progress bar with `XMLHttpRequest` (supports `onprogress`)
- Metadata preview before upload (format, size, checksum)

---

**5. Project Selector (Live Switch)**

Currently a login is for a single project per session.
The CloudX Dashboard has a project switcher in the header.

```jsx
const ProjectSelector = () => {
  const { token, switchProject } = useAuth();
  return (
    <Select value={token.project} onChange={switchProject}>
      {projects.map(p => <Option key={p.id}>{p.name}</Option>)}
    </Select>
  );
};
```

**Work:**
- Endpoint `POST /auth/tokens` with a new `scope.project`
- Store the new token in the context
- Re-fetch all resources after the switch

---

**6. Quota Usage Display**

The dashboard overview must show usage vs quota.

```jsx
const QuotaWidget = ({ resource }) => (
  <div>
    <span>{resource.used} / {resource.limit} {resource.unit}</span>
    <ProgressBar
      value={resource.used / resource.limit * 100}
      color={resource.used > resource.limit * 0.8 ? "red" : "green"}
    />
  </div>
);
```

**Work:**
- The placement `GET /usages` endpoint already exists — expose it to the dashboard
- Add a quota endpoint to Block Storage and Identity
- A new widget on the Overview page

---

#### 🟡 Medium Priority

**7. Identity: Roles Management**

An admin page to manage role assignments per user per project.

```
Admin -> Users -> alice -> Roles -> [admin, member, reader] (checkboxes)
Admin -> Projects -> myproject -> Members -> [alice:admin, bob:member]
```

---

**8. API Access Panel**

Show credentials and endpoint URLs for each service.

```
Service Catalog:
  Identity: http://localhost:8005/v3
  Compute:  http://localhost:8003

Access Keys:
  Access Key ID: AKxxxxxx
  Secret: [Copy] [Regenerate]
```

---

**9. Resource Detail Pages**

Currently many resources only appear in a list without a detail page.
Needed: Server detail page, Volume detail page, Network detail page.

---

#### 🟢 Low Priority

- **Object Storage UI (Swift/MinIO)** — Outside the base services scope
- **Heat / Orchestration UI** — The service is not implemented yet

### CloudX Dashboard Effort Estimate

| Work | Complexity | Effort |
|---|:---:|---|
| noVNC console | High | 3–5 days |
| Live log streaming | Low | 1 day |
| Network topology view | High | 3–5 days |
| Image upload via UI | Medium | 2 days |
| Project selector | Medium | 1–2 days |
| Quota usage display | Low | 1 day |
| Roles management | Medium | 2 days |
| API access panel | Low | 1 day |

**Target after completion: ~82%**

---

## 9. Cross-Cutting Infrastructure

Several cross-service pieces of work that need to be done together.

### 9.1 API Versioning ✅ (Done in Phase 3)

In CloudX all services use versioning: `/v2/`, `/v3/`, microversions.
Currently cloudx has no version prefix.

```
Currently:    GET /servers
CloudX:   GET /v2.1/servers  (Compute)
             GET /v3/tokens     (Identity)
             GET /v2/images     (Image)
```

**Work:**
- Add a version prefix to every service router
- An `X-CloudX-Request-Id` header on every response
- A `GET /` endpoint (version discovery)

---

### 9.2 Request ID Tracing

CloudX generates an `x-openstack-request-id` for every request.

```python
@app.middleware("http")
async def add_request_id(request: Request, call_next):
    req_id = f"req-{uuid.uuid4()}"
    response = await call_next(request)
    response.headers["x-openstack-request-id"] = req_id
    return response
```

**Work:**
- Add the middleware to all services
- Propagate the request ID in RPC calls between services

---

### 9.3 Centralised Quota System

Instead of each service having its own quota, it is better to have one consistent
quota system (similar to `oslo.quota`).

```python
# services/shared/quota.py:
class QuotaEngine:
    def reserve(self, project_id, resource, delta): ...
    def commit(self, reservations): ...
    def rollback(self, reservations): ...
```

---

### 9.4 Pagination Standard

All services must use a consistent pagination format.

```
GET /servers?limit=20&marker=<last_server_id>
# response + a "next" link if there is a next page
```

---

### 9.5 Event Notification Standard

Currently only compute and block-store have event notifications.
All services must be able to publish standard events.

```python
# Standard CloudX event types:
"compute.instance.create.start"
"compute.instance.create.end"
"volume.create.start"
"volume.create.end"
"network.create.end"
```

---

### 9.6 Health Check Standard

All services must implement a uniform health check endpoint.

```
GET /healthcheck -> {"status": "up", "checks": {"db": "ok", "rabbitmq": "ok"}}
```

---

## 10. Priorities and Effort Estimates

### Impact vs Effort Matrix

```
High Impact ────────────────────────────────────────────────
                   | Quick Wins (Low Effort)   | Big Bets (High Effort)  |
                   |---------------------------|-------------------------|
                   | Server tags               | noVNC console           |
                   | Lock/unlock server        | IPv6 support            |
                   | Image tags                | LVM thin provisioning   |
                   | Shared networks           | QoS enforcement         |
                   | Service catalog           | Network topology UI     |
───────────────────|---------------------------|-------------------------|
Low Impact         | API microversions         | Volume migration        |
                   | Request ID tracing        | Volume replication      |
                   | Health check standard     | Consistency groups      |
                   | Pagination standard       | BGP routing             |
                   Low Effort                       High Effort
```

---

### ✅ Phase 1 — Quick Wins *(Done)*

| # | Work | Service | Status |
|---|---|---|:---:|
| 1 | Server tags | Compute | ✅ |
| 2 | Lock/unlock server | Compute | ✅ |
| 3 | Image tags | Image | ✅ |
| 4 | Image members (sharing) | Image | ✅ |
| 5 | Shared networks | Networking | ✅ |
| 6 | Basic quota management | Block Storage | ✅ |
| 7 | Pagination standard for all services | Cross-cutting | ✅ |
| 8 | Request ID tracing | Cross-cutting | ✅ |
| 9 | Service catalog | Identity | ✅ |
| 10 | Quota display in the dashboard | CloudX Dashboard | ✅ |
| 11 | Live log streaming in the dashboard | CloudX Dashboard | ✅ |
| 12 | Image upload via UI | CloudX Dashboard | ✅ |
| 13 | Project selector | CloudX Dashboard | ✅ |

**Score after Phase 1:**
Identity ~75% · Compute ~84% · Networking ~68% · Image ~80% · Block Storage ~80% · CloudX Dashboard ~68%

---

### ✅ Phase 2 — Core Features *(Done)*

| # | Work | Service | Status |
|---|---|---|:---:|
| 1 | noVNC proxy + console UI | Compute + CloudX Dashboard | ✅ |
| 2 | Allocation candidates | Placement | ✅ |
| 3 | Resize confirm/revert | Compute | ✅ |
| 4 | Shelve/unshelve | Compute | ✅ |
| 5 | QoS policies (OVN-based) | Networking | ✅ |
| 6 | Domain management | Identity | ✅ |
| 7 | Task-based image import | Image | ✅ |
| 8 | Subnet pools | Networking | ✅ |
| 9 | Network topology view | CloudX Dashboard | ✅ |
| 10 | API microversions | Placement | ✅ |
| 11 | Reshaper endpoint | Placement | ✅ |

**Score after Phase 2:**
Identity ~82% · Compute ~90% · Networking ~78% · Image ~86% · Block Storage ~82% · Placement ~85% · CloudX Dashboard ~78%

---

### ✅ Phase 3 — Polish *(Done)*

| # | Work | Service | Status |
|---|---|---|:---:|
| 1 | IPv6 support (SLAAC, DHCPv6 stateful/stateless) | Networking | ✅ |
| 2 | LVM thin provisioning (`lvm_type=thin`, auto thin pool) | Block Storage | ✅ |
| 3 | Multi-attach volume (`multiattach=true`) | Block Storage | ✅ |
| 4 | Central oslo.policy RBAC (`shared/policy.py`) | Identity | ✅ |
| 5 | Per-port DNAT port forwarding | Networking | ✅ |
| 6 | Nested resource providers + Aggregates | Placement | ✅ |
| 7 | API version discovery (`GET /`, `GET /v2.1`, and so on) | Cross-cutting | ✅ |

**Score after Phase 3:**
Identity ~87% · Compute ~90% · Networking ~82% · Image ~84% · Block Storage ~88% · Placement ~88% · CloudX Dashboard ~70%

---

### ✅ Phase 4 — Advanced Integration (Done)

This phase focuses on advanced features that raise parity significantly,
mainly on the **CloudX Dashboard**, **cross-service integration**, and **hardening** of the existing system.

#### 🔴 High Priority

| # | Work | Service | Status | Notes |
|---|---|---|:---:|---|
| 1 | **noVNC Console (In-Browser)** | Compute + CloudX Dashboard | ✅ | Websockify container, WebSocket token auth |
| 2 | **Network Topology View** | CloudX Dashboard | ✅ | React Flow / Cytoscape.js, click a node → detail |
| 3 | **Application Credentials** | Identity | ✅ | `/v3/users/{id}/application_credentials`, scoped roles |
| 4 | **Multi-Scope Token** (domain/system scope) | Identity | ✅ | Extend the JWT with a `scope_type` field |
| 5 | **Ceph QoS Enforcement** | Block Store Worker | ✅ | `rbd config image set rbd_qos_iops_limit` |
| 6 | **Quota Reserved State** (atomic reserve) | Block Storage | ✅ | Reserve before create, roll back on error |
| 7 | **Custom Resource Classes** | Placement | ✅ | `PUT /resource_classes/CUSTOM_GPU_A100` |
| 8 | **Forbidden Traits Filter** in allocation_candidates | Placement | ✅ | `?required=X&forbidden=Y` query params |
| 9 | **Identity: Roles Management UI** | CloudX Dashboard | ✅ | Admin page to manage role assignments |
| 10 | **API Access Panel** | CloudX Dashboard | ✅ | Shows the endpoint catalog + access keys |

#### 🟡 Medium Priority

| # | Work | Service | Status | Notes |
|---|---|---|:---:|---|
| 11 | **Trunk Ports** (VLAN-in-VLAN) | Networking | ✅ | OVN parent_port, API wrapping |
| 12 | **DNS Integration** (OVN internal DNS) | Networking | ✅ | Port `dns_name` column, `DNS` table in the OVN NB |
| 13 | **Dual-Stack Port** (IPv4 + IPv6 at once) | Networking | ✅ | `fixed_ips` array with two entries |
| 14 | **Image Signing & Verification** | Image + Compute | ✅ | Hash signature in properties, verified on download |
| 15 | **Image Advanced Filtering** | Image | ✅ | `?min_disk=`, `?disk_format=`, `?sort=` |
| 16 | **Volume Migration** (LVM ↔ Ceph) | Block Storage | ✅ | `qemu-img convert`, atomic swap after the copy |
| 17 | **Aggregates & AZ** (Compute) | Compute | ✅ | `/os-aggregates`, AZ derived automatically from the aggregate |
| 18 | **Resource Detail Pages** | CloudX Dashboard | ✅ | Server detail, Volume detail, Network detail pages |
| 19 | **Enforce policy.py on every endpoint** | Cross-cutting | ✅ | Integrate `policy.enforce()` per action |
| 20 | **Centralised Quota System** (`shared/quota.py`) | Cross-cutting | ✅ | oslo.quota-inspired, atomic reserve/commit/rollback |

#### 🟢 Low Priority

| # | Work | Service | Status | Notes |
|---|---|---|:---:|---|
| 21 | Consistency Groups | Block Storage | ✅ | Atomic volume group snapshot |
| 22 | Image Caching (TTL) | Image | ✅ | Metadata cache, optional Redis |
| 23 | Health Check Standard | Cross-cutting | ✅ | `GET /healthcheck` → `{db: ok, rabbitmq: ok}` |
| 24 | Event Notification Standard | Cross-cutting | ✅ | `compute.instance.create.start/end` in all services |

**Target score after Phase 4:**
Identity ~93% · Compute ~94% · Networking ~88% · Image ~90% · Block Storage ~93% · Placement ~92% · CloudX Dashboard ~85%

---

### Overall Estimate

| Phase | Duration | Item count | Status |
|---|:---:|:---:|:---:|
| ✅ Phase 1 — Quick Wins | 2–3 weeks | 13 items | **Done** |
| ✅ Phase 2 — Core Features | 3–5 weeks | 11 items | **Done** |
| ✅ Phase 3 — Polish | 2–4 weeks | 7 items | **Done** |
| ✅ Phase 4 — Advanced Integration | 4–6 weeks | 24 items | **Done** |
| 🚀 Phase 5 — Production Readiness | 4–6 weeks | 10 items | **Next** |
| **Total (P1–P5)** | **15–24 weeks** | **65 items** | — |

---

### 🚀 Phase 5 — Production Readiness & Day-2 Operations (Estimate: 4–6 weeks)

After all core services (base services) reach a high level of parity with CloudX, the focus shifts to operational stability, scalability, and hardening so that CloudX is ready for a pseudo-production or more serious lab environment.

#### 🔴 High Priority
| # | Work | Area | Effort | Notes |
|---|---|---|:---:|---|
| 1 | **High Availability (HA) API Services** | Infrastructure | 3-5 days | ✅ HAProxy multi-backend round-robin, RabbitMQ 3-node cluster (`docker-compose.ha.yml`). |
| 2 | **Database Replication & Backup** | Infrastructure | 4-5 days | ✅ PostgreSQL primary+standby streaming replication, 23:00 Asia/Jakarta cron backup to MinIO. |
| 3 | **Centralised Logging & Telemetry** | Cross-cutting | 5-7 days | ✅ Prometheus (15d retention) + Grafana + exporters + `/metrics` per service. |

#### 🟡 Medium Priority
| # | Work | Area | Effort | Notes |
|---|---|---|:---:|---|
| 4 | **Zero-Downtime Migrations** | Infrastructure | 3-4 days | ✅ Alembic scaffold (`services/migrations/`), baseline + additive-first rules. |
| 5 | **CI/CD & Automated Testing** | Infrastructure | 5 days | ⏭️ Deferred (not yet planned by the user). |
| 6 | **Security Hardening (TLS/SSL)** | Cross-cutting | 3-4 days | ✅ Internal PKI (`services/certs/generate-certs.sh`), internal service-to-service TLS only. |
| 7 | **Volume Backups to Object Storage** | Block Storage | 3-4 days | ✅ `POST /volumes/{id}/backup` + worker export → MinIO, restore into a new volume. |

#### 🟢 Low Priority (Ecosystem Expansion)
| # | Work | Area | Effort | Notes |
|---|---|---|:---:|---|
| 8 | **Heat (Orchestration)** | New Service | 7-10 days | ⏭️ Deferred to a later phase. |
| 9 | **Octavia (Load Balancer)** | New Service | 10-14 days | ⏭️ Deferred to a later phase. |
| 10| **Magnum (Container Service)** | New Service | 10-14 days | ⏭️ Deferred to a later phase. |

**Note:** Adaptive multi-node deployment (Control Plane / Compute / Storage / Gateway)
was implemented through separate compose overlays, and remains compatible with single-node mode.
Operational details: `docs/fase5-production-readiness.md`.

**End goal of Phase 5:** CloudX is no longer just an API lab, but a self-contained cloud system that is resilient, secure, and observable.

---

## 11. Target Architecture

### Additional Components Needed

```
                          ┌─────────────────────────────┐
                          │       Dashboard :8080        │
                          │  React + noVNC + Topology    │ <-- Browser
                          └──────────────┬──────────────┘
                                         │
         ┌───────────────────────────────┼─────────────────────────┐
         │                               │                         │
  ┌──────▼──────┐             ┌──────────▼──────┐      ┌──────────▼──────┐
  │  auth-api   │             │  compute-api    │      │ networking-api  │
  │  + service  │             │  + shelve/lock  │      │  + QoS + IPv6   │
  │    catalog  │             │  + server tags  │      │  + subnet pools │
  └──────┬──────┘             └────────┬────────┘      └────────┬────────┘
         │                             │                         │
         │                    ┌────────▼────────┐      ┌────────▼────────┐
         │               ┌────│ compute-worker  │      │   OVN NB DB     │
         │               │    │  + VNC port RPC │      │   + QoS table   │
         │               │    └─────────────────┘      └─────────────────┘
         │               │
         │          ┌────▼────────────┐    <-- NEW
         │          │  novnc-proxy    │  :6080
         │          │  (websockify)   │
         │          └─────────────────┘
         │
  ┌──────▼──────┐   ┌──────────────────┐   ┌──────────────────────┐
  │  image-api  │   │  block-store-api │   │    placement-api     │
  │  + members  │   │  + quota + thin  │   │  + alloc_candidates  │
  │  + tags     │   │  + multi-attach  │   │  + nested providers  │
  │  + signing  │   │  + Ceph QoS      │   │  + custom RC         │
  └─────────────┘   └──────────────────┘   └──────────────────────┘

  PostgreSQL — tables that already exist (Phases 1–3) ✅:
    services, endpoints (service catalog)
    domains (domain management)
    server_tags, image_members, image_tasks
    volume_quotas, networking_port_forwardings
    subnet_pools, qos_policies, qos_rules
    image_tags, placement_rp_aggregates

  PostgreSQL — new tables (Phase 4):
    application_credentials (Identity)
    os_aggregates, aggregate_hosts (Compute)
    networking_trunks, networking_trunk_subports (Networking)
    resource_classes (Placement)
    quota_reservations (Cross-cutting)
```

### Database Tables — Status

#### ✅ Already Implemented (Phases 1–3)

| Table | Service | Purpose |
|---|---|---|
| `auth_services` | Identity | Service catalog entries |
| `auth_endpoints` | Identity | Endpoint per service per region |
| `auth_domains` | Identity | Domain hierarchy |
| `compute_server_tags` | Compute | Server tagging |
| `image_members` | Image | Image sharing between projects |
| `image_tasks` | Image | Async import tasks |
| `block_store_volume_quotas` | Block Storage | Hard limit per project |
| `networking_subnet_pools` | Networking | Subnet address pool |
| `networking_qos_policies` | Networking | QoS policy definitions |
| `networking_qos_rules` | Networking | Bandwidth limit rules |
| `networking_port_forwardings` | Networking | Per-port DNAT forwarding |
| `placement_rp_aggregates` | Placement | RP → aggregate association |
| `auth_application_credentials` | Identity | Application credentials |
| `compute_aggregates` | Compute | Host aggregate definitions |
| `compute_aggregate_hosts` | Compute | Host → aggregate membership |
| `networking_trunks` | Networking | Trunk port parent |
| `networking_trunk_subports` | Networking | VLAN sub-port entries |
| `placement_resource_classes` | Placement | Custom resource classes |
| `quota_reservations` | Cross-cutting | Atomic quota reserve tracking |
| `block_store_consistency_groups` | Block Storage | Consistency Groups metadata |
| `block_store_cgsnapshots` | Block Storage | Consistency Group Snapshots |

#### 🚀 Needed in Phase 5 (Optional / Extensions)

| Table | Service | Purpose |
|---|---|---|
| `block-store_backups` | Block Storage | Volume backup metadata for MinIO |
| `heat_stacks` | Heat | Stores orchestration stack state |
| `octavia_loadbalancers` | Octavia | Load balancer metadata |

---

## 📌 Important Notes

> **Note 1 — Lab Scope**
> Some features are **deliberately not implemented** because they are outside the single-node lab scope:
> Live migration, Evacuation, Multi-node scheduling, FWaaS, VPNaaS, LBaaS (Octavia),
> Barbican, Designate, Swift, Heat.

> **Note 2 — The Backends Are Already Real**
> CloudX already uses real backends (libvirt, OVN, LVM, Ceph).
> Most of the remaining work is in the **API layer** and the **UI**, not in the backend.

> **Note 3 — CloudX Client Compatibility**
> To be usable with the `openstack` CLI or the standard CloudX SDK,
> the main priorities are:
> 1. A service catalog in Identity (clients auto-discover endpoints)
> 2. API prefix versioning (`/v2/`, `/v3/`)
> 3. A response format that matches the Identity v3 token format

---

## 📊 Progress Tracker

| Phase | Total Items | Done | Progress |
|---|:---:|:---:|:---:|
| Phase 1 — Quick Wins | 13 | 13 | ████████████ 100% |
| Phase 2 — Core Features | 11 | 11 | ████████████ 100% |
| Phase 3 — Polish | 7 | 7 | ████████████ 100% |
| Phase 4 — Advanced Integration | 24 | 24 | ████████████ 100% |
| Phase 5 — Production Readiness | 10 | 7 | ████████░░░░ 70% |
| **Overall** | **65** | **62** | **███████████░ 95%** |

---

*This document was written from an analysis of the code in `services/`, `docs/feature-comparison.md`, and `README.md`.*
*Last updated: End of August 2026 — Phase 4 done, Phase 5 (Production Readiness) planned.*
