# 📊 Feature Comparison: cloudx vs Standard Private Cloud

> This document maps the features implemented in the **cloudx private cloud lab**
> against a standard cloud specification, with an estimated similarity percentage.
>
> **Updated:** August 2026 — covers Phases 1–4 (base-service API parity) and
> Phase 5 (production readiness: multi-node, HA, backup, monitoring, internal TLS,
> AWS Cloudscape Dashboard).

---

## 🔢 Overall Summary

| cloudx service | CloudX equivalent | Initial | After P1–P4 | After P5 |
|---|---|:---:|:---:|:---:|
| `auth-api` | **Identity** | 0% | ~93% | **~95%** |
| `compute-api` + `compute-worker` | **Compute** | ~45% | ~94% | **~95%** |
| `networking-api` | **Networking** | ~55% | ~88% | **~90%** |
| `image-api` | **Image** | ~40% | ~90% | **~92%** |
| `block-store-api` | **Block Storage** | ~35% | ~93% | **~95%** |
| `metadata-api` | **Metadata API** | ~75% | ~92% | **~92%** |
| `placement-api` | **Placement** | ~15% | ~92% | **~93%** |
| `dashboard` | **CloudX Dashboard** | 0% | ~85% | **~90%** |
| *(none)* | **Heat** | 0% | 0% | 0% |
| *(none)* | **Swift** | 0% | 0% | 0% (MinIO as a substitute) |
| *(none)* | **Ironic** | 0% | 0% | 0% |

**Overall average (implemented services): ~92%**
*(vs ~72% before Phases 1–4 started)*

### Phase 5 Additions — Production Readiness (cross-service)

| Area | Feature | Status |
|---|---|:---:|
| Infrastructure | Adaptive multi-node deployment (Control Plane / Compute / Storage) | ✅ |
| Infrastructure | High Availability: RabbitMQ cluster + PostgreSQL streaming replication | ✅ |
| Infrastructure | HAProxy multi-backend round-robin + Keepalived VIP | ✅ |
| Backup | Automated DB backup to MinIO (cron 23:00 Asia/Jakarta, tiered retention) | ✅ |
| Backup | Full volume backup to object storage + restore (`POST /volumes/{id}/backup`) | ✅ |
| Observability | Prometheus (15-day retention) + pre-provisioned Grafana dashboards | ✅ |
| Observability | `/metrics` endpoint on all API services + alert rules | ✅ |
| Security | Internal TLS PKI (`services/certs/generate-certs.sh`, service-to-service only) | ✅ |
| Database | Alembic migration scaffold (baseline stamp, additive-first) | ✅ |
| CloudX Dashboard | UI redesigned in the style of the AWS Console with the Cloudscape Design System | ✅ |

---

## 1. Identity — `auth-api`

### ✅ Existing Features

#### Tokens & Authentication
| Feature | Endpoint | Status |
|---|---|:---:|
| Login (username + password + project) | `POST /auth/tokens` | ✅ |
| Token validation | `GET /auth/tokens` | ✅ |
| Logout (revoke token) | `DELETE /auth/tokens` | ✅ |
| JWT-based token (stateless) | Internal | ✅ |
| Token expiry (configurable) | `AUTH_TOKEN_EXPIRE_HOURS` | ✅ |

#### Project Management
| Feature | Endpoint | Status |
|---|---|:---:|
| Create project | `POST /projects` | ✅ |
| List projects | `GET /projects` | ✅ |
| Show project | `GET /projects/{ref}` | ✅ |
| Delete project | `DELETE /projects/{ref}` | ✅ |
| Default project `admin` auto-bootstrap | Startup | ✅ |

#### User Management
| Feature | Endpoint | Status |
|---|---|:---:|
| Create user | `POST /users` | ✅ |
| List users | `GET /users` | ✅ |
| Show user | `GET /users/{ref}` | ✅ |
| Delete user | `DELETE /users/{ref}` | ✅ |
| Role assignment (admin/member/reader) | Integrated | ✅ |
| Default admin user auto-bootstrap | Startup | ✅ |

#### Group Management
| Feature | Endpoint | Status |
|---|---|:---:|
| Create group | `POST /groups` | ✅ |
| List / Show / Delete group | `GET\|DELETE /groups` | ✅ |
| Add / Remove user to/from a group | `POST\|DELETE /groups/{ref}/users` | ✅ |

#### Access Key (AWS-style CLI Auth)
| Feature | Endpoint | Status |
|---|---|:---:|
| Create access key | `POST /access-keys` | ✅ |
| List access keys | `GET /access-keys` | ✅ |
| Delete access key | `DELETE /access-keys/{key_id}` | ✅ |
| Validate access key | `POST /auth/access-key` | ✅ |

### ❌ Missing Features (vs. the real Identity service)
| Feature | Notes |
|---|---|
| **Service Catalog** | No endpoint discovery catalog |
| **Domain** (multi-domain hierarchy) | Only one domain |
| **Scoped tokens** (domain/system scope) | Project scope only |
| **Federation / SAML / OAuth2** | No SSO |
| **Application credentials** | Similar to access keys, but no secret rotation |
| **Multi-factor authentication** | None |
| **Token audit / Fernet token** | JWT only, no Fernet |
| **Endpoint registration** | No service catalog API |
| **Policy (oslo.policy)** | Simple RBAC, not oslo.policy |
| **Trust** (delegated token) | None |

**Estimated Identity similarity: ~65%**

---

## 2. Compute (Compute API) — `compute-api` + `compute-worker`

### ✅ Existing Features

#### Server Lifecycle
| Feature | Endpoint | Status |
|---|---|:---:|
| Create server (boot VM) | `POST /servers` | ✅ |
| List servers | `GET /servers` | ✅ |
| Show server detail | `GET /servers/{ref}` | ✅ |
| Start server | `POST /servers/{ref}/start` | ✅ |
| Stop server (graceful shutdown) | `POST /servers/{ref}/stop` | ✅ |
| Reboot server (soft & hard) | `POST /servers/{ref}/reboot` | ✅ |
| **Pause** (hypervisor-level suspend) | `POST /servers/{ref}/pause` | ✅ |
| **Resume** from pause | `POST /servers/{ref}/resume` | ✅ |
| **Suspend** (managed save to disk) | `POST /servers/{ref}/suspend` | ✅ |
| **Unsuspend** (restore from managed save) | `POST /servers/{ref}/unsuspend` | ✅ |
| **Resize** (live vCPU + RAM hotplug) | `POST /servers/{ref}/resize` | ✅ |
| **Rebuild** (replace the root disk from a new image) | `POST /servers/{ref}/rebuild` | ✅ |
| **Rescue** (boot from a rescue image) | `POST /servers/{ref}/rescue` | ✅ |
| **Unrescue** (return to the root disk) | `POST /servers/{ref}/unrescue` | ✅ |
| Delete server | `DELETE /servers/{ref}` | ✅ |
| Delete + keep root volume | `DELETE /servers/{ref}?keep_root_volume=true` | ✅ |
| Snapshot a server to an image | `POST /servers/{ref}/snapshots` | ✅ |

#### Server Console & Diagnostics
| Feature | Endpoint | Status |
|---|---|:---:|
| Serial console (virsh console) | `GET /servers/{ref}/console` | ✅ |
| Console URL (VNC info) | `GET /servers/{ref}/console-url` | ✅ |
| Console log (PTY output) | `GET /servers/{ref}/logs` | ✅ |
| SSH info (IP + keypair) | `GET /servers/{ref}/ssh-info` | ✅ |
| Password injection via QEMU GA | `POST /servers/{ref}/password` | ✅ |

#### Metadata & User Data
| Feature | Endpoint | Status |
|---|---|:---:|
| Get server metadata | `GET /servers/{ref}/metadata` | ✅ |
| Get user-data | `GET /servers/{ref}/user-data` | ✅ |
| Set user-data | `PUT /servers/{ref}/user-data` | ✅ |

#### Flavor
| Feature | Endpoint | Status |
|---|---|:---:|
| Create flavor (vCPU, RAM, Disk) | `POST /flavors` | ✅ |
| List / Show / Delete flavor | `GET\|DELETE /flavors` | ✅ |
| Default flavors (nano, micro, tiny, small, medium) | Bootstrap | ✅ |

#### Keypair
| Feature | Endpoint | Status |
|---|---|:---:|
| Generate keypair (RSA 2048) | `POST /keypairs` | ✅ |
| Import keypair (public key) | `POST /keypairs/import` | ✅ |
| List / Show / Delete keypair | `GET\|DELETE /keypairs` | ✅ |

#### Server Groups
| Feature | Endpoint | Status |
|---|---|:---:|
| Create server group | `POST /server-groups` | ✅ |
| Policy: affinity / anti-affinity | Integrated | ✅ |
| List / Show / Delete server group | `GET\|DELETE /server-groups` | ✅ |
| Affinity enforcement on server create | Integrated in `POST /servers` | ✅ |

#### Network & Volume Hotplug
| Feature | Endpoint | Status |
|---|---|:---:|
| Attach a port to a server (multi-NIC) | `POST /servers/{ref}/ports` | ✅ |
| Detach a port from a server | `DELETE /servers/{ref}/ports/{port_ref}` | ✅ |
| Attach a volume (LVM/Ceph) to a server | `POST /servers/{ref}/volumes/attach` | ✅ |
| Detach a volume from a server | `POST /servers/{ref}/volumes/detach` | ✅ |

#### Host
| Feature | Endpoint | Status |
|---|---|:---:|
| Host check (KVM, libvirt, OVS) | `GET /host/check` | ✅ |
| Host resource info | `GET /host` | ✅ |

#### Compute Backend (compute-worker)
| Feature | Status |
|---|:---:|
| Provisioning via **libvirt/QEMU/KVM** | ✅ |
| Boot from an **LVM block device** (`/dev/VG/LV`) | ✅ |
| Boot from **Ceph RBD** (network disk) | ✅ |
| NoCloud ISO config drive for cloud-init | ✅ |
| Inject an SSH public key via cloud-init | ✅ |
| Tap interface + OVS binding | ✅ |
| OVN port binding per VM | ✅ |
| libvirt XML auto-detect: block / network / file | ✅ |

### ❌ Missing Features (vs. the real Compute service)
| Feature | Notes |
|---|---|
| **Live Migration** | Single-node, no live migration |
| **Evacuation** | Single-node only |
| **Aggregates / Availability Zones** | Deliberately excluded |
| **Separate ephemeral disk** | Volume-first, no ephemeral disk |
| **VNC/SPICE console via noVNC** | Serial console only (VNC port exists but is not proxied yet) |
| **Multi-node scheduling** | Single-node only |
| **vfat config drive format** | ISO9660 only |
| **Trusted image certificates** | None |
| **Cold migration / resize confirm/revert** | Resize is live-only (hotplug), no confirm/revert |
| **Shelve / Unshelve** | None |
| **Lock / Unlock server** | None |
| **Server tags** | None |
| **Trigger crash dump** | None |

**Estimated Compute similarity: ~80%**

---

## 3. Networking (Networking API) — `networking-api`

### ✅ Existing Features

#### Network & Subnet
| Feature | Endpoint | Status |
|---|---|:---:|
| Create network (internal/tenant) | `POST /networks` | ✅ |
| Create external/provider network | `POST /networks` (`external=true`) | ✅ |
| List / Show / Delete network | `GET\|DELETE /networks` | ✅ |
| Create subnet with CIDR, gateway, DNS | `POST /subnets` | ✅ |
| DHCP via OVN DHCP options | Integrated | ✅ |
| IPAM (automatic IP allocation) | Integrated in port | ✅ |
| List / Show / Delete subnet | `GET\|DELETE /subnets` | ✅ |
| `no-dhcp` option for provider subnets | Integrated | ✅ |

#### Router & L3
| Feature | Endpoint | Status |
|---|---|:---:|
| Create router | `POST /routers` | ✅ |
| Set gateway (external network) | `POST /routers/{ref}/gateway` | ✅ |
| Add interface (subnet) | `POST /routers/{ref}/interfaces` | ✅ |
| Remove interface | `DELETE /routers/{ref}/interfaces/{subnet}` | ✅ |
| List / Show / Delete router | `GET\|DELETE /routers` | ✅ |
| OVN Logical Router | OVN backend | ✅ |
| Gateway chassis binding (OVN) | OVN backend | ✅ |

#### Port
| Feature | Endpoint | Status |
|---|---|:---:|
| Create port (manual) | `POST /ports` | ✅ |
| List / Show / Delete port | `GET\|DELETE /ports` | ✅ |
| Set security group on a port | `POST /ports/{ref}/security-groups` | ✅ |
| Clear security groups | `POST /ports/{ref}/security-groups/clear` | ✅ |
| Enable / Disable port security | `POST /ports/{ref}/security/enable\|disable` | ✅ |
| Inspect a port's OVN state | `GET /ports/{ref}/ovn` | ✅ |
| Automatic MAC address | Integrated | ✅ |

#### Floating IP
| Feature | Endpoint | Status |
|---|---|:---:|
| Create floating IP | `POST /floating-ips` | ✅ |
| Associate FIP with a server/port | `POST /floating-ips/{ref}/associate` | ✅ |
| Disassociate FIP | `POST /floating-ips/{ref}/disassociate` | ✅ |
| Delete FIP | `DELETE /floating-ips/{ref}` | ✅ |
| List / Show FIP | `GET /floating-ips` | ✅ |
| OVN DNAT+SNAT NAT rule | OVN backend | ✅ |

#### Security Group
| Feature | Endpoint | Status |
|---|---|:---:|
| Create security group | `POST /security-groups` | ✅ |
| List / Show / Delete SG | `GET\|DELETE /security-groups` | ✅ |
| Add rule (ingress/egress, TCP/UDP/ICMP/any) | `POST /security-groups/{ref}/rules` | ✅ |
| Delete rule | `DELETE /security-group-rules/{id}` | ✅ |
| List rules per SG | `GET /security-groups/{ref}/rules` | ✅ |
| Automatic default SG per project | Bootstrap | ✅ |
| OVN Port Group + ACL translation | OVN backend | ✅ |
| Remote group (an SG referencing another SG) | Integrated | ✅ |
| Inspect an SG's OVN state | `GET /security-groups/{ref}/ovn` | ✅ |
| Port-level security group enforcement | OVN ACL | ✅ |

#### Provider Network (OVN Localnet)
| Feature | Status |
|---|:---:|
| OVN `localnet` port to `br-ex` | ✅ |
| Bridge mapping `physnet1:br-ex` | ✅ |
| ARP proxy in OVN | ✅ |
| Gateway chassis binding | ✅ |

#### OVN Diagnostics
| Feature | Endpoint | Status |
|---|---|:---:|
| Show the whole OVN topology | `GET /ovn/show` | ✅ |

### ❌ Missing Features (vs. the real Networking service)
| Feature | Notes |
|---|---|
| **QoS (Quality of Service)** | No bandwidth limiting |
| **Load Balancer (Octavia)** | Deliberately excluded |
| **VPN as a Service** | Deliberately excluded |
| **Firewall as a Service (FWaaS)** | Deliberately excluded |
| **BGP Dynamic Routing** | Advertise and learn (BIRD, eBGP IPv4), not 1:1; see docs/bgp-dynamic-routing.md |
| **Network Segment** | No manual VLAN/VXLAN segments |
| **Trunk ports (VLAN-in-VLAN)** | None |
| **IPv6** | IPv4 only |
| **DNS Integration (Designate)** | Per-port `dns_name` only; zones and recordsets are in the separate DNS service (docs/dns.md) |
| **RBAC policies per network** | Multi-tenancy by project, not network RBAC |
| **Port forwarding** | No per-port DNAT |
| **Address scopes** | None |
| **Shared networks** | No network sharing between projects |
| **Subnet pools / address pools** | Manual IPAM per subnet |

**Estimated Networking similarity: ~65%**

---

## 4. Image (Image API) — `image-api`

### ✅ Existing Features

| Feature | Endpoint | Status |
|---|---|:---:|
| Upload image (multipart file) | `POST /images` | ✅ |
| Import an image from a local path | `POST /images/import-path` | ✅ |
| List images | `GET /images` | ✅ |
| Show image detail | `GET /images/{ref}` | ✅ |
| Download image | `GET /images/{ref}/download` | ✅ |
| Delete image | `DELETE /images/{ref}` | ✅ |
| Update image metadata | `PATCH /images/{ref}` | ✅ |
| **Deactivate image** | `POST /images/{ref}/deactivate` | ✅ |
| **Reactivate image** | `POST /images/{ref}/reactivate` | ✅ |
| **Visibility: public / private** | Integrated + project scoping | ✅ |
| Format: `qcow2`, `raw`, `iso` | Integrated | ✅ |
| Backend: **local file** | Default | ✅ |
| Backend: **MinIO** (S3-compatible) | `IMAGE_STORAGE_BACKEND=minio` | ✅ |
| Image metadata (name, disk_format, size, checksum) | Integrated | ✅ |
| Project scoping (private image per project) | Integrated | ✅ |

### ❌ Missing Features (vs. the real Image service)
| Feature | Notes |
|---|---|
| **Image member** (share an image between projects) | No explicit sharing |
| **Image tags** | No tagging |
| **Image signing & verification** | No Barbican integration |
| **Import tasks** (async via the task API) | Upload is synchronous |
| **Multi-store backend** (Ceph, Swift, HTTP) | File + MinIO only (deliberately limited) |
| **Image encryption** | None |
| **Pagination & advanced filtering** | Basic filters only |
| **Image caching** (dedicated cache service) | Cache only in compute-worker |
| **Image conversion** (automatic format conversion) | No task-based conversion |

**Estimated Image similarity: ~70%**

---

## 5. Block Storage — `block-store-api`

### ✅ Existing Features

#### Volume Lifecycle
| Feature | Endpoint | Status |
|---|---|:---:|
| Create an empty volume | `POST /volumes` | ✅ |
| Create a volume from an image (boot volume) | `POST /volumes` (`image=...`) | ✅ |
| Create a volume from a snapshot | `POST /volumes` (`snapshot=...`) | ✅ |
| Create a volume from another volume (clone) | `POST /volumes` (`source_volume=...`) | ✅ |
| List / Show volume | `GET /volumes` | ✅ |
| Delete volume | `DELETE /volumes/{ref}` | ✅ |
| Attach a volume to a server | `POST /volumes/{ref}/attach` | ✅ |
| Detach a volume from a server | `POST /volumes/{ref}/detach` | ✅ |
| Force detach | `POST /volumes/{ref}/force-detach` | ✅ |
| Detach all volumes from a server | `POST /servers/{id}/volumes/detach-all` | ✅ |
| **Extend volume** (resize) | `POST /volumes/{ref}/extend` | ✅ |
| **Backup volume** (snapshot as backup) | `POST /volumes/{ref}/backup` | ✅ |

#### Volume Types — AWS EBS-style Performance Profiles
| Feature | Endpoint | Status |
|---|---|:---:|
| List volume types (alphabetical) | `GET /volume-types` | ✅ |
| Create volume type (admin) | `POST /volume-types` | ✅ |
| Show volume type | `GET /volume-types/{ref}` | ✅ |
| Update volume type (mutable fields) | `PATCH /volume-types/{ref}` | ✅ |
| Delete volume type (admin) | `DELETE /volume-types/{ref}` | ✅ |
| **Delete protection while the type is in use** | Embedded | ✅ |
| **No auto-bootstrap** — an admin must create them | Design | ✅ |
| Field `disk_type`: `sata_ssd`/`sas_ssd`/`nvme_ssd`/`sata_hdd`/`sas_hdd` | Schema | ✅ |
| Field `max_iops` (informational, not enforced) | Schema | ✅ |
| Field `max_throughput` MB/s (informational) | Schema | ✅ |
| Field `backend`: `lvm` or `ceph` (infrastructure) | Schema | ✅ |

#### Storage Backend
| Feature | Status |
|---|:---:|
| **LVM backend** (block device `/dev/VG/LV`) | ✅ |
| **Auto-create VG** from `LVM_PV_NAME` at startup | ✅ |
| **Ceph RBD backend** (network disk `rbd:pool/image`) | ✅ |
| Both backends can be active at the same time | ✅ |
| `STORAGE_LVM_DRIVER` + `STORAGE_CEPH_RBD_DRIVER` toggles | ✅ |
| Image-to-LVM: `qemu-img convert -O raw ... /dev/VG/LV` | ✅ |
| Image-to-Ceph: `qemu-img convert ... rbd:pool/image` | ✅ |
| LVM snapshot (COW, 20% size) | ✅ |
| Ceph RBD snapshot | ✅ |

#### Snapshot
| Feature | Endpoint | Status |
|---|---|:---:|
| Create a snapshot from a volume | `POST /volumes/{ref}/snapshots` | ✅ |
| Create a snapshot (standalone) | `POST /snapshots` | ✅ |
| List / Show / Delete snapshot | `GET\|DELETE /snapshots` | ✅ |

#### Boot-from-Volume
| Feature | Status |
|---|:---:|
| Boot a VM from an LVM root volume | ✅ |
| Boot a VM from a Ceph RBD root volume | ✅ |
| Volume-first architecture (all VMs boot from a volume) | ✅ |
| Delete a VM + keep the root volume | ✅ |

### ❌ Missing Features (vs. the real Block Storage service)
| Feature | Notes |
|---|---|
| **Volume encryption** | No Barbican integration |
| **Volume migration** (between backends) | No online migration |
| **Volume replication** | Cross-host replica, promote/failover (full copy, not 1:1) |
| **Consistency groups** | None |
| **Generic volume groups** | None |
| **Quota management** | No per-project volume quota |
| **Multi-attach volume** | 1 server per volume |
| **iSCSI / NFS / FC backend** | LVM + Ceph only |
| **LVM thin provisioning** | Thick LVM only |
| **QoS enforcement** | max_iops/max_throughput are informational only, no cgroups/rate limit |

**Estimated Block Storage similarity: ~75%**

---

## 6. Metadata API — `metadata-api`

### ✅ Existing Features

#### EC2-compatible Metadata (cloud-init compatible)
| Endpoint | Status |
|---|:---:|
| `/{version}/meta-data/` (index) | ✅ |
| `/{version}/meta-data/instance-id` | ✅ |
| `/{version}/meta-data/hostname` | ✅ |
| `/{version}/meta-data/local-hostname` | ✅ |
| `/{version}/meta-data/instance-type` | ✅ |
| `/{version}/meta-data/local-ipv4` | ✅ |
| `/{version}/meta-data/public-ipv4` | ✅ |
| `/{version}/meta-data/public-hostname` | ✅ |
| `/{version}/meta-data/ami-launch-index` | ✅ |
| `/{version}/meta-data/block-device-mapping/` | ✅ |
| `/{version}/meta-data/public-keys/` | ✅ |
| `/{version}/meta-data/public-keys/0/openssh-key` | ✅ |
| `/{version}/user-data` | ✅ |

#### CloudX Metadata Format
| Endpoint | Status |
|---|:---:|
| `/openstack/latest/meta_data.json` | ✅ |
| `/openstack/latest/network_data.json` (multi-NIC) | ✅ |
| `/openstack/latest/vendor_data.json` | ✅ |
| **`/openstack/latest/vendor_data2.json`** (v2 format) | ✅ |
| `/openstack/latest/user_data` | ✅ |
| `/openstack/latest/password` | ✅ |

#### Instance Identity Document
| Endpoint | Status |
|---|:---:|
| **`/openstack/latest/identity`** (JSON document) | ✅ |
| **`/openstack/latest/identity/document`** (raw JSON) | ✅ |
| **`/openstack/latest/identity/signature`** (HMAC-SHA256) | ✅ |
| HMAC signing key via `METADATA_HMAC_KEY` (optional) | ✅ |

#### Lookup Mechanism
| Feature | Status |
|---|:---:|
| Lookup by source IP (X-Real-IP) | ✅ |
| Lookup by `X-Forwarded-For` header | ✅ |
| Lookup by `instance_id` query param | ✅ |
| Lookup by `port_id` query param | ✅ |
| Lookup by `fixed_ip` query param | ✅ |
| Metadata proxy (`metadata-proxy`) | ✅ |
| iptables DNAT `169.254.169.254:80` | ✅ |

#### `meta_data.json` Fields
| Field | Status |
|---|:---:|
| `uuid`, `name`, `hostname` | ✅ |
| `project_id` | ✅ |
| `availability_zone` | ✅ |
| `instance_type` (flavor name) | ✅ |
| `created_at` | ✅ |
| `public_keys` (keypair) | ✅ |
| `random_seed` | ✅ |

### ❌ Missing Features
| Feature | Notes |
|---|---|
| **Dynamic vendor_data2** via webhook | The `dynamic` section is empty |
| **X.509 instance identity cert** | HMAC only, no PKI |
| **`launch-index`** for multi-instance | Always 0 |

**Estimated Metadata API similarity: ~92%**

---

## 7. Placement — `placement-api`

### ✅ Existing Features

#### Resource Provider
| Feature | Endpoint | Status |
|---|---|:---:|
| Create resource provider | `POST /resource_providers` | ✅ |
| List / Show resource provider | `GET /resource_providers` | ✅ |
| Delete resource provider | `DELETE /resource_providers/{uuid}` | ✅ |

#### Inventory
| Feature | Endpoint | Status |
|---|---|:---:|
| Set inventory (VCPU, MEMORY_MB, DISK_GB) | `PUT /resource_providers/{uuid}/inventories` | ✅ |
| Get inventory | `GET /resource_providers/{uuid}/inventories` | ✅ |

#### Traits
| Feature | Endpoint | Status |
|---|---|:---:|
| List traits | `GET /traits` | ✅ |
| Set traits on a provider | `PUT /resource_providers/{uuid}/traits` | ✅ |
| Get a provider's traits | `GET /resource_providers/{uuid}/traits` | ✅ |

#### Allocations
| Feature | Endpoint | Status |
|---|---|:---:|
| Create an allocation (consume resources) | `POST /allocations` | ✅ |
| Get allocations per consumer (server) | `GET /allocations/{consumer_uuid}` | ✅ |
| Delete an allocation | `DELETE /allocations/{consumer_uuid}` | ✅ |

#### Usages
| Feature | Endpoint | Status |
|---|---|:---:|
| Usage per project (quota-like) | `GET /usages` | ✅ |
| Usage per resource provider | `GET /resource_providers/{uuid}/usages` | ✅ |

### ❌ Missing Features (vs. the real Placement service)
| Feature | Notes |
|---|---|
| **Nested Resource Providers** | No provider hierarchy |
| **Allocation candidates** (`GET /allocation_candidates`) | No scheduling hints |
| **Reshaper** (atomic inventory+allocation) | None |
| **Aggregate association** | No aggregates |
| **Forbidden traits** | No negative trait filtering |
| **Microversion** header | No API versioning |
| **RBAC** at the placement level | Basic auth token only |

**Estimated Placement similarity: ~70%**

---

## 8. CloudX Dashboard (Web Dashboard) — `dashboard`

### ✅ Existing Features

#### AWS Console-style Shell & Navigation (Cloudscape Design System)
| Feature | Status |
|---|:---:|
| **TopNavigation bar** — identity, global search jump-to-service | ✅ |
| **Dark / Light mode toggle** in the header (persisted via localStorage) | ✅ |
| Project switcher (multi-project scope) in the utilities menu | ✅ |
| User menu (API access, access keys, roles, sign out) | ✅ |
| AppLayout + SideNavigation per service group + automatic breadcrumbs | ✅ |
| Notification Flashbar (old toasts fully migrated) | ✅ |

#### Compute Pages
| Feature | Status |
|---|:---:|
| Instances — Cloudscape Table with multi-select, filter, pagination | ✅ |
| Bulk instance state actions (start/stop/reboot/delete) | ✅ |
| Actions dropdown: pause/resume/suspend/rescue/shelve/lock/resize confirm-revert | ✅ |
| Launch instance modal (flavor/image/network/keypair select) | ✅ |
| Instance detail tabs: Details / Networking / Storage / Console | ✅ |
| **noVNC console** embedded (modal + detail tab + full page) | ✅ |
| Live console log with 2-second auto-refresh | ✅ |
| Flavors, Key Pairs, Server Groups CRUD | ✅ |

#### Storage Pages
| Feature | Status |
|---|:---:|
| Volumes — table + create/extend/snapshot/detach modals | ✅ |
| Volume backup to object storage + restore from the detail page | ✅ |
| Snapshots, Volume Types (with AWS EBS presets) | ✅ |

#### Networking Pages
| Feature | Status |
|---|:---:|
| Networks (network + subnet modal), Routers, Floating IPs | ✅ |
| Security Groups with a rules manager (inbound/outbound, AWS-style) | ✅ |
| Network detail tabs (Details/Subnets/Ports) | ✅ |
| Network topology graph (force-directed SVG) + node inspector panel | ✅ |

#### Admin & Overview
| Feature | Status |
|---|:---:|
| Overview dashboard: stat cards, allocation charts, quota progress bars | ✅ |
| API Access panel (endpoint catalog from the Identity service catalog) | ✅ |
| Admin: Projects / Users / Access Keys (tabs) | ✅ |
| Role management with an inline role selector | ✅ |
| WebSocket live events → auto-refresh of the instances & volumes tables | ✅ |

### ❌ Missing Features (vs. the real CloudX Dashboard)
| Feature | Notes |
|---|---|
| **Heat / Orchestration UI** | The Heat service is not implemented |
| **Object Storage (Swift) UI** | MinIO is only an image/backup backend, not a file manager UI |

**Estimated CloudX Dashboard similarity: ~90%**
*(UI redesigned with the AWS Cloudscape Design System in Phase 5)*

---

## 9. Services That Do Not Exist At All

| Cloud service | Function | Notes |
|---|---|---|
| **Heat** | Orchestration / IaC (stack templates) | No template engine |
| **Swift** | Object storage | MinIO is used instead as the image backend |
| **Ironic** | Bare-metal provisioning | Single-node VMs only |
| **Magnum** | Container orchestration (Kubernetes-as-a-Service) | K3s clusters in CloudX VMs, not 1:1; see docs/k8s-engine.md |
| **Octavia** | Load balancer as a Service | Deliberately excluded |
| **Barbican** | Secret / certificate management | None |
| **Designate** | DNS as a Service | BIND9 zones, recordsets, secondaries and automatic names, not 1:1; see docs/dns.md |
| **Manila** | Shared file system (NFS, CIFS) | NFSv4 shares via NFS-Ganesha, access rules, extend; no snapshots, quotas or CIFS, not 1:1; see docs/file-store.md |
| **Zun** | Container service (Docker) | Single containers with local storage, hardened by default; no block volumes or exec, not 1:1; see docs/container.md |
| **Masakari** | Instance HA / failover | Heartbeat host monitor, iSCSI fencing, evacuate and a self-fencing watchdog; no IPMI/STONITH, ha-api is a single point of failure, not 1:1; see docs/ha.md |
| **Trove** | Database as a Service | PostgreSQL and MariaDB on a VM or container, crash-consistent volume backup and restore; no guest agent, replication or PITR, not 1:1; see docs/database.md |
| **Blazar** | Resource reservation | None |
| **Senlin** | Clustering service | None |

---

## 📈 Percentage Comparison Chart

```
Service         Initial   After P1–P4   After P5
──────────────────────────────────────────────────────
Identity           0%  ██████████████  ~93%  ██████████████▌  ~95%
Compute              45%  ██████████████  ~94%  ██████████████▌  ~95%
Networking           55%  █████████████   ~88%  █████████████▌   ~90%
Image            40%  █████████████   ~90%  █████████████▌   ~92%
Block Storage            35%  █████████████   ~93%  █████████████▌   ~95%
Metadata          75%  █████████████   ~92%  █████████████    ~92%
Placement         15%  █████████████   ~92%  █████████████▌   ~93%
CloudX Dashboard            0%  ████████████    ~85%  █████████████    ~90% (*)

(*) The CloudX Dashboard was redesigned with the AWS Cloudscape Design System in Phase 5.
```

---

## 📝 Assessment Methodology

The similarity percentage is calculated along three dimensions:

1. **API endpoint coverage** — what percentage of the main CloudX endpoints are implemented
2. **Behavioural fidelity** — whether the business logic behaves the same (response format, error codes, lifecycle states)
3. **Authentic backend** — whether real components are used (libvirt, OVN, LVM, Ceph) rather than mocks/simulations

> **Note:** The percentages are **qualitative estimates**, not a formal measurement. Some features are deliberately
> excluded (LBaaS, multi-AZ, and so on), not because they cannot be built, but because they are outside the scope of this lab.
> CloudX has hundreds of sub-features per service; cloudx focuses on the **operational happy path** that is enough
> to run real cloud infrastructure on a single node.

### Deliberately Excluded Features (by design)
| Feature | Reason |
|---|---|
| Aggregates / Availability Zones | Single-node, not relevant |
| Ephemeral disk | Volume-first architecture — every disk is a persistent volume |
| Load Balancer (Octavia) | High complexity, out of scope |
| VPN as a Service | Out of scope |
| FWaaS | Security groups are enough for the lab |
| BGP Dynamic Routing | Out of scope |
| Multi-store image backend | File + MinIO is enough |
