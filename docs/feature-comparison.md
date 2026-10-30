# 📊 Feature Comparison: cloudx vs Standard Private Cloud

> This document maps the features implemented in **cloudx** against a standard private cloud specification,
> with an estimated similarity percentage.
>
> **Updated:** October 2026, for release 1.0.1. The endpoint tables were checked against the routes in
> `services/*/app`; the percentages are estimates (see [Assessment Methodology](#-assessment-methodology)).

---

## 🔢 Overall Summary

| cloudx service | CloudX equivalent | Estimated similarity |
|---|---|:---:|
| `auth-api` | **Identity** | ~75% |
| `compute-api` + `compute-worker` | **Compute** | ~85% |
| `networking-api` | **Networking** | ~80% |
| `image-api` | **Image** | ~78% |
| `block-store-api` | **Block Storage** | ~82% |
| `metadata-api` | **Metadata API** | ~92% |
| `placement-api` | **Placement** | ~82% |
| `dashboard` | **CloudX Dashboard** | ~90% |

**Average over these eight services: ~83%**

The services below have no percentage because they are partial re-implementations, "not 1:1" with their counterparts.
Section 9 says what each one covers: Heat, Swift, Barbican, Octavia, Ceilometer/Aodh, CloudKitty, Panko, Designate,
Manila, Zun, Magnum, Masakari, Trove, Blazar and Zaqar. Ironic and Senlin (as a separate service) do not exist.

### Production Readiness (cross-service)

Status: ✅ shipped and used in the native (.deb) deployment, ✗ not available.

| Area | Feature | Status |
|---|---|:---:|
| Infrastructure | Adaptive multi-node deployment (Control Plane / Compute / Storage) | ✅ |
| Infrastructure | Instance HA: host monitor, evacuation, self-fencing watchdog, second `ha-api` (see docs/ha.md) | ✅ |
| Infrastructure | HA for the control plane's own RabbitMQ and PostgreSQL (not provided; use your distro's tooling) | ✗ |
| Infrastructure | HAProxy multi-backend round-robin + Keepalived VIP in front of the APIs (not provided) | ✗ |
| Backup | PostgreSQL backup job for CloudX's own databases (not provided; schedule `pg_dump` yourself) | ✗ |
| Backup | Full volume backup to object storage + restore (`POST /volumes/{id}/backup`), with schedules | ✅ |
| Observability | `/metrics` endpoint on all API services (bearer token) | ✅ |
| Observability | Prometheus scrape config, alert rules and Grafana dashboards (not provided; you run your own) | ✗ |
| Security | Internal TLS between services | ✗ |
| Database | Alembic migrations, one history per service, `cloudx-manage db sync` | ✅ |
| Packaging | OpenStack-style `.deb` packages and a service catalog in `auth-api` | ✅ |
| CloudX Dashboard | UI in the style of the AWS Console with the Cloudscape Design System | ✅ |

---

## 1. Identity — `auth-api`

### ✅ Existing Features

#### Tokens & Authentication
| Feature | Endpoint | Status |
|---|---|:---:|
| Login (username + password + project) | `POST /auth/tokens` | ✅ |
| Token validation | `GET /auth/tokens/validate` | ✅ |
| Logout | `DELETE /auth/tokens/current` | ✅ |
| Switch the project of a session | `POST /session/switch-project` | ✅ |
| JWT-based token (stateless) | Internal | ✅ |
| Token expiry (configurable) | `AUTH_TOKEN_EXPIRE_HOURS` | ✅ |

#### Project Management
| Feature | Endpoint | Status |
|---|---|:---:|
| Create project | `POST /projects` | ✅ |
| List projects | `GET /projects` | ✅ |
| Show / update project | `GET\|PUT /projects/{ref}` | ✅ |
| Delete project | `DELETE /projects/{ref}` | ✅ |
| Project quota | `GET\|PUT /projects/{ref}/quota` | ✅ |
| Default project `admin` auto-bootstrap | Startup | ✅ |

#### User Management
| Feature | Endpoint | Status |
|---|---|:---:|
| Create user | `POST /users` | ✅ |
| List users | `GET /users` | ✅ |
| Show / update user | `GET\|PUT /users/{ref}` | ✅ |
| Delete user | `DELETE /users/{ref}` | ✅ |
| Role assignment per project (admin/member/reader) | `PUT\|DELETE /projects/{project}/users/{user}/roles/{role}` | ✅ |
| List role assignments | `GET /role-assignments` | ✅ |
| Default admin user auto-bootstrap | Startup | ✅ |

#### Group Management
| Feature | Endpoint | Status |
|---|---|:---:|
| Create group | `POST /groups` | ✅ |
| List / Delete group | `GET\|DELETE /groups` | ✅ |
| Add / Remove user to/from a group | `PUT\|DELETE /groups/{ref}/members/{user_ref}` | ✅ |

#### Domains
| Feature | Endpoint | Status |
|---|---|:---:|
| Create / list / show / update / delete domain | `/v3/domains` | ✅ |
| Domain quota | `GET\|PUT\|DELETE /v3/domains/{id}/quota` | ✅ |
| Domain isolation (a domain only sees its own projects) | Integrated | ✅ |

#### Service Catalog
| Feature | Endpoint | Status |
|---|---|:---:|
| Register / list / delete a service | `/v3/services` | ✅ |
| Register / list / delete an endpoint | `/v3/endpoints` | ✅ |
| Catalog for the current token | `GET /v3/auth/catalog` | ✅ |
| Catalog sync from the packages | `cloudx-manage catalog sync` | ✅ |

#### Access Key & Application Credentials
| Feature | Endpoint | Status |
|---|---|:---:|
| Create access key | `POST /access-keys` | ✅ |
| List access keys | `GET /access-keys` | ✅ |
| Delete access key | `DELETE /access-keys/{key_id}` | ✅ |
| Token from an access key | `POST /auth/tokens/access-key` | ✅ |
| Create / list / show / delete application credential | `/v3/users/{user}/application_credentials` | ✅ |
| Token from an application credential | `POST /auth/tokens/application-credential` | ✅ |

#### Policy & Audit
| Feature | Endpoint | Status |
|---|---|:---:|
| Central RBAC policy map for all services | `GET /policy` | ✅ |
| Check an action against the policy | `POST /policy/enforce` | ✅ |
| Audit log | `GET\|POST /audit-logs` | ✅ |

### ❌ Missing Features (vs. the real Identity service)
| Feature | Notes |
|---|---|
| **Scoped tokens** (domain/system scope) | Login issues project-scoped tokens only |
| **Federation / SAML / OAuth2** | No SSO |
| **Application credential rotation** | Create and delete only |
| **Multi-factor authentication** | None |
| **Fernet token** | JWT only |
| **Trust** (delegated token) | None |
| **Policy files (oslo.policy)** | A central rule map, not oslo.policy files |

**Estimated Identity similarity: ~75%**

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
| **Confirm / revert resize** | `POST /servers/{ref}/confirmResize\|revertResize` | ✅ |
| **Rebuild** (replace the root disk from a new image) | `POST /servers/{ref}/rebuild` | ✅ |
| **Rescue** (boot from a rescue image) | `POST /servers/{ref}/rescue` | ✅ |
| **Unrescue** (return to the root disk) | `POST /servers/{ref}/unrescue` | ✅ |
| **Shelve / Unshelve** | `POST /servers/{ref}/shelve\|unshelve` | ✅ |
| **Lock / Unlock** | `POST /servers/{ref}/lock\|unlock` | ✅ |
| Delete server | `DELETE /servers/{ref}` | ✅ |
| Delete + keep root volume | `DELETE /servers/{ref}?keep_root_volume=true` | ✅ |
| Snapshot a server to an image | `POST /servers/{ref}/snapshots` | ✅ |

#### Migration & Recovery
| Feature | Endpoint | Status |
|---|---|:---:|
| Live migration to a target host | `POST /servers/{ref}/live-migrate` | ✅ |
| Cold migration | `POST /servers/{ref}/migrate` | ✅ |
| List possible target hosts | `GET /servers/{ref}/migration-candidates` | ✅ |
| Evacuate a server from a failed host | `POST /servers/{ref}/evacuate` | ✅ |
| Automatic recovery of protected VMs | `ha-api` (docs/ha.md) | ✅ |

#### Server Console & Diagnostics
| Feature | Endpoint | Status |
|---|---|:---:|
| Serial console (virsh console) | `GET /servers/{ref}/console` | ✅ |
| Console URL (VNC info + noVNC link) | `GET /servers/{ref}/console-url` | ✅ |
| Remote console request | `POST /servers/{ref}/remote-consoles` | ✅ |
| Console log (PTY output) | `GET /servers/{ref}/logs` | ✅ |
| Server metrics | `GET /servers/{ref}/metrics` | ✅ |
| Instance action history | `GET /servers/{ref}/os-instance-actions` | ✅ |
| SSH info (IP + keypair) | `GET /servers/{ref}/ssh-info` | ✅ |
| Password injection via QEMU GA | `POST /servers/{ref}/password` | ✅ |

#### Metadata, User Data & Tags
| Feature | Endpoint | Status |
|---|---|:---:|
| Get server metadata | `GET /servers/{ref}/metadata` | ✅ |
| Get / set user-data | `GET\|PUT /servers/{ref}/user-data` | ✅ |
| Server tags | `GET\|PUT /servers/{ref}/tags`, `PUT\|DELETE /servers/{ref}/tags/{tag}` | ✅ |

#### Flavor
| Feature | Endpoint | Status |
|---|---|:---:|
| Create flavor (vCPU, RAM, Disk) | `POST /flavors` | ✅ |
| List / Show / Delete flavor | `GET\|DELETE /flavors` | ✅ |
| Flavor extra specs | `/flavors/{ref}/os-extra_specs` | ✅ |
| Default flavors (tiny, small, medium) | Bootstrap | ✅ |

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

#### Aggregates, Zones, Quotas & Services
| Feature | Endpoint | Status |
|---|---|:---:|
| Host aggregates | `/os-aggregates` | ✅ |
| Availability zones | `GET /os-availability-zone`, `availability_zone` on create | ✅ |
| Compute quota | `GET\|PUT /os-quota-sets/{project}` | ✅ |
| Absolute limits | `GET /limits` | ✅ |
| Compute service list and update | `GET /os-services`, `PUT /os-services/{host}` | ✅ |

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
| Boot from an **LVM block device** (`/dev/VG/LV`), local or over iSCSI | ✅ |
| Boot from **Ceph RBD** (network disk) | ✅ |
| NoCloud ISO config drive for cloud-init | ✅ |
| Inject an SSH public key via cloud-init | ✅ |
| Tap interface + OVS binding | ✅ |
| OVN port binding per VM | ✅ |
| libvirt XML auto-detect: block / network / file | ✅ |
| Multi-node scheduling through placement (`select_destination`) | ✅ |

### ❌ Missing Features (vs. the real Compute service)
| Feature | Notes |
|---|---|
| **Separate ephemeral disk** | Volume-first, no ephemeral disk (flavors carry `ephemeral_gb` but it is not used) |
| **noVNC proxy** | `console-url` returns a noVNC link (`NOVNC_BASE_URL`, default `localhost:6080`); no package or unit in this repo starts a proxy on that port |
| **vfat config drive format** | ISO9660 only |
| **Trusted image certificates** | None |
| **Trigger crash dump** | None |

**Estimated Compute similarity: ~85%**

---

## 3. Networking (Networking API) — `networking-api`

### ✅ Existing Features

#### Network & Subnet
| Feature | Endpoint | Status |
|---|---|:---:|
| Create network (internal/tenant) | `POST /networks` | ✅ |
| Create external/provider network | `POST /networks` (`external=true`) | ✅ |
| Shared network (visible to other projects) | `POST /networks` (`shared=true`) | ✅ |
| List / Show / Delete network | `GET\|DELETE /networks` | ✅ |
| Create subnet with CIDR, gateway, DNS | `POST /subnets` | ✅ |
| DHCP via OVN DHCP options | Integrated | ✅ |
| IPAM (automatic IP allocation) | Integrated in port | ✅ |
| List / Show / Delete subnet | `GET\|DELETE /subnets` | ✅ |
| `no-dhcp` option for provider subnets | Integrated | ✅ |
| Subnet pools | `/subnetpools` | ✅ |
| IPv6 subnets (`ip_version: 6`, RA and address modes, optional gateway; the CLI takes the version from `--cidr`) | `POST /subnets` | ✅ |

#### Router & L3
| Feature | Endpoint | Status |
|---|---|:---:|
| Create router | `POST /routers` | ✅ |
| Update router | `PUT\|PATCH /routers/{ref}` | ✅ |
| Set gateway (external network) | `POST /routers/{ref}/gateway` | ✅ |
| Add interface (subnet) | `POST /routers/{ref}/interfaces` | ✅ |
| Remove interface | `DELETE /routers/{ref}/interfaces/{subnet}` | ✅ |
| List / Show / Delete router | `GET\|DELETE /routers` | ✅ |
| VPC peering between routers | `POST /routers/{ref}/peering`, `/vpc-peerings` | ✅ |
| OVN Logical Router | OVN backend | ✅ |
| Gateway chassis binding (OVN) | OVN backend | ✅ |

#### Port
| Feature | Endpoint | Status |
|---|---|:---:|
| Create port (manual) | `POST /ports` | ✅ |
| List / Show / Update / Delete port | `GET\|PUT\|PATCH\|DELETE /ports` | ✅ |
| Set security group on a port | `POST /ports/{ref}/security-groups` | ✅ |
| Clear security groups | `POST /ports/{ref}/security-groups/clear` | ✅ |
| Enable / Disable port security | `POST /ports/{ref}/security/enable\|disable` | ✅ |
| Inspect a port's OVN state | `GET /ports/{ref}/ovn` | ✅ |
| Automatic MAC address | Integrated | ✅ |
| IPv6 on a port: automatic on a network with no IPv4 subnet; opt-in with `fixed_ips` where an IPv4 subnet exists. The address is allocated and checked | `POST /ports` | ✅ |
| Trunk ports with subports | `/trunks`, `PUT /trunks/{ref}/add_subports\|remove_subports` | ✅ |

#### Floating IP
| Feature | Endpoint | Status |
|---|---|:---:|
| Create floating IP | `POST /floating-ips` | ✅ |
| Associate FIP with a server/port | `POST /floating-ips/{ref}/associate` | ✅ |
| Disassociate FIP | `POST /floating-ips/{ref}/disassociate` | ✅ |
| Delete FIP | `DELETE /floating-ips/{ref}` | ✅ |
| List / Show FIP | `GET /floating-ips` | ✅ |
| Port forwarding on a FIP | `/floatingips/{fip}/port_forwardings` | ✅ |
| OVN DNAT+SNAT NAT rule | OVN backend | ✅ |

#### Security Group
| Feature | Endpoint | Status |
|---|---|:---:|
| Create security group | `POST /security-groups` | ✅ |
| List / Show / Delete SG | `GET\|DELETE /security-groups` | ✅ |
| Add rule (ingress/egress, TCP/UDP/ICMP/any) | `POST /security-groups/{ref}/rules` | ✅ |
| Delete rule | `DELETE /security-group-rules/{id}` | ✅ |
| List rules per SG | `GET /security-groups/{ref}/rules` | ✅ |
| IPv6 rules (`ethertype`; `--ethertype` in the CLI) | `POST /security-groups/{ref}/rules` | ✅ |
| The default group has the same rules for IPv6 as for IPv4 (egress, ssh, ICMPv6) | Bootstrap | ✅ |
| Rule presets | `GET /security-groups/presets`, `POST /security-groups/{ref}/apply-preset` | ✅ |
| Automatic default SG per project | Bootstrap | ✅ |
| OVN Port Group + ACL translation | OVN backend | ✅ |
| Remote group (an SG referencing another SG) | Integrated | ✅ |
| Inspect an SG's OVN state | `GET /security-groups/{ref}/ovn` | ✅ |
| Port-level security group enforcement | OVN ACL | ✅ |

#### QoS
| Feature | Endpoint | Status |
|---|---|:---:|
| QoS policies with bandwidth limit rules | `/qos/policies`, `/qos/policies/{id}/bandwidth_limit_rules` | ✅ |
| Per-port rate limit (`qos_max_kbps`, `qos_burst_kbps`) | `POST\|PUT /ports` | ✅ |

#### VPN & BGP
| Feature | Endpoint | Status |
|---|---|:---:|
| Site-to-site VPN: IKE policy, IPsec policy, site connection | `/vpn/ikepolicies`, `/vpn/ipsecpolicies`, `/vpn/site-connections` | ✅ |
| BGP peers | `/bgp-peers` | ✅ |

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
| **Load Balancer** | Separate service, see section 9 (`load-balancer-api`), not in `networking-api` |
| **Firewall as a Service (FWaaS)** | Deliberately excluded |
| **BGP Dynamic Routing** | Advertise and learn (BIRD, eBGP IPv4), not 1:1; see docs/bgp-dynamic-routing.md |
| **VPN as a Service** | Site-to-site only (strongSwan), see docs/vpn-site-to-site.md |
| **IPv6 router advertisements (SLAAC)** | OVN sends no RA: nothing sets `ipv6_ra_configs` on router ports. A VM gets its IPv6 address and prefix from the config drive (static, like IPv4), so cloud-init images work; DHCPv6 options are created and bound to ports but have not been tried against a guest |
| **Network Segment** | No manual VLAN/VXLAN segments; the segment fields in responses are empty |
| **DNS Integration (Designate)** | Per-port `dns_name` only; zones and recordsets are in the separate DNS service (docs/dns.md) |
| **RBAC policies per network** | A `shared` flag per network, no per-project RBAC entries |
| **Address scopes** | None (the fields in responses are empty) |
| **QoS rule types** | Bandwidth limit only |

**Estimated Networking similarity: ~80%**

---

## 4. Image (Image API) — `image-api`

### ✅ Existing Features

| Feature | Endpoint | Status |
|---|---|:---:|
| Upload image (multipart file) | `POST /images` | ✅ |
| Import an image from a local path | `POST /images/import-path` | ✅ |
| List images (filters, sort, `limit`) | `GET /images` | ✅ |
| Show image detail | `GET /images/{ref}` | ✅ |
| Download image | `GET /images/{ref}/download` | ✅ |
| Delete image | `DELETE /images/{ref}` | ✅ |
| Update image metadata | `PATCH /images/{ref}` | ✅ |
| **Deactivate image** | `POST /images/{ref}/deactivate` | ✅ |
| **Reactivate image** | `POST /images/{ref}/reactivate` | ✅ |
| **Image members** (share with another project) | `/images/{ref}/members` | ✅ |
| **Image tags** | `PUT\|DELETE /images/{ref}/tags/{tag}` | ✅ |
| **Sign and verify** | `POST /images/{ref}/sign`, `GET /images/{ref}/verify` | ✅ |
| **Tasks** (async import) | `/v2/tasks` | ✅ |
| **Prefetch** to the compute nodes' cache | `POST /images/{ref}/prefetch` | ✅ |
| **Visibility: public / private** | Integrated + project scoping | ✅ |
| Format: `qcow2`, `raw`, `iso` | Integrated | ✅ |
| Backend: **local file** | Default | ✅ |
| Backend: **MinIO** (S3-compatible) | `IMAGE_STORAGE_BACKEND=minio` | ✅ |
| Image metadata (name, disk_format, size, checksum) | Integrated | ✅ |
| Project scoping (private image per project) | Integrated | ✅ |

### ❌ Missing Features (vs. the real Image service)
| Feature | Notes |
|---|---|
| **Multi-store backend** (Ceph, Swift, HTTP) | File + MinIO only (deliberately limited) |
| **Image encryption** | None |
| **Pagination** | `limit` only, no `marker` |
| **Dedicated image cache service** | Prefetch to compute nodes only |
| **Image conversion** (automatic format conversion) | None |

**Estimated Image similarity: ~78%**

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
| Delete volume (refused while it has snapshots) | `DELETE /volumes/{ref}` | ✅ |
| Attach a volume to a server | `POST /volumes/{ref}/attach` | ✅ |
| Detach a volume from a server | `POST /volumes/{ref}/detach` | ✅ |
| Force detach | `POST /volumes/{ref}/force-detach` | ✅ |
| Detach all volumes from a server | `POST /servers/{id}/volumes/detach-all` | ✅ |
| **Extend volume** (resize) | `POST /volumes/{ref}/extend` | ✅ |
| **Multi-attach** (when created with `multiattach=true`) | `POST /volumes/{ref}/attach` | ✅ |
| **Migrate volume** (cold, `available` volumes only) | `POST /volumes/{ref}/action` (`os-migrate_volume`) | ✅ |
| **Volume actions** (reset status, force delete) | `POST /volumes/{ref}/action` | ✅ |

#### Backup
| Feature | Endpoint | Status |
|---|---|:---:|
| Backup a volume to object storage | `POST /volumes/{ref}/backup` | ✅ |
| List / show / delete backups | `GET\|DELETE /backups` | ✅ |
| Restore a backup into a new volume | `POST /backups/{id}/restore` | ✅ |
| Scheduled backups | `POST\|GET /volumes/{ref}/schedules`, `DELETE /schedules/{id}` | ✅ |

#### Replication
| Feature | Endpoint | Status |
|---|---|:---:|
| Enable / disable replication | `POST /volumes/{ref}/enable-replication\|disable-replication` | ✅ |
| Sync and failover | `POST /volumes/{ref}/sync-replication\|failover` | ✅ |

#### Consistency Groups & Quotas
| Feature | Endpoint | Status |
|---|---|:---:|
| Consistency groups | `/consistencygroups` | ✅ |
| Consistency group snapshots | `/cgsnapshots` | ✅ |
| Volume quota per project | `GET\|PUT\|DELETE /os-quota-sets/{project}` | ✅ |

#### Volume Types — AWS EBS-style Performance Profiles
| Feature | Endpoint | Status |
|---|---|:---:|
| List volume types (alphabetical) | `GET /volume-types` | ✅ |
| Create volume type (admin) | `POST /volume-types` | ✅ |
| Show volume type | `GET /volume-types/{ref}` | ✅ |
| Update volume type (`description`, `max_iops`, `max_throughput`, `is_public`) | `PATCH /volume-types/{ref}` | ✅ |
| Delete volume type (admin) | `DELETE /volume-types/{ref}` | ✅ |
| **Delete protection while the type is in use** | Embedded | ✅ |
| **No auto-bootstrap** — an admin must create them | Design | ✅ |
| Field `disk_type`: `sata_ssd`/`sas_ssd`/`nvme_ssd`/`sata_hdd`/`sas_hdd` | Schema | ✅ |
| Field `max_iops` | Schema | ✅ |
| Field `max_throughput` MB/s | Schema | ✅ |
| Field `backend`: `lvm` or `ceph` (infrastructure) | Schema | ✅ |

#### Storage Backend
| Feature | Status |
|---|:---:|
| **LVM backend** (block device `/dev/VG/LV`), thick or thin (`lvm_type: thin`) | ✅ |
| **Auto-create VG** from `LVM_PV_NAME` at startup | ✅ |
| **Ceph RBD backend** (network disk `rbd:pool/image`) | ✅ |
| Both backends can be active at the same time | ✅ |
| `STORAGE_LVM_DRIVER` + `STORAGE_CEPH_RBD_DRIVER` toggles | ✅ |
| LVM volumes shared over **iSCSI** between nodes (`ISCSI_EXPORT`) | ✅ |
| NFS file driver (`qcow2` files on a shared mount) | ✅ |
| Image-to-LVM: `qemu-img convert -O raw ... /dev/VG/LV` | ✅ |
| Image-to-Ceph: `qemu-img convert ... rbd:pool/image` | ✅ |
| LVM snapshot (COW, 20% size) | ✅ |
| Ceph RBD snapshot | ✅ |
| QoS on Ceph volumes (`rbd_qos_iops_limit`, `rbd_qos_bps_limit` from the volume type) | ✅ |

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
| **Volume encryption** | The `encrypted` field is always false; key-manager is not wired in |
| **Online volume migration** | Cold only, the volume must be `available` |
| **Volume replication** | Cross-host replica, promote/failover (full copy, not 1:1) |
| **Generic volume groups** | Consistency groups only |
| **FC backend** | LVM (with iSCSI), Ceph and NFS only |
| **QoS enforcement on LVM** | `max_iops` / `max_throughput` are applied only on Ceph; LVM volumes ignore them |

**Estimated Block Storage similarity: ~82%**

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
| Create resource provider (optionally with `parent_provider_id`) | `POST /resource_providers` | ✅ |
| List / Show resource provider (`in_tree` and parent filters) | `GET /resource_providers` | ✅ |
| Delete resource provider | `DELETE /resource_providers/{uuid}` | ✅ |
| Heartbeat from a compute node | `GET\|POST /resource_providers/{uuid}/heartbeat` | ✅ |
| List compute nodes | `GET /compute_nodes` | ✅ |

#### Inventory
| Feature | Endpoint | Status |
|---|---|:---:|
| Set inventory (VCPU, MEMORY_MB, DISK_GB) | `PUT /resource_providers/{uuid}/inventories` | ✅ |
| Get inventory | `GET /resource_providers/{uuid}/inventories` | ✅ |

#### Resource Classes, Traits & Aggregates
| Feature | Endpoint | Status |
|---|---|:---:|
| List / show / create / delete resource class | `/resource_classes` | ✅ |
| List traits | `GET /traits` | ✅ |
| Set traits on a provider | `PUT /resource_providers/{uuid}/traits` | ✅ |
| Get a provider's traits | `GET /resource_providers/{uuid}/traits` | ✅ |
| Provider aggregates | `GET\|PUT /resource_providers/{uuid}/aggregates` | ✅ |

#### Allocations & Scheduling
| Feature | Endpoint | Status |
|---|---|:---:|
| Create an allocation (consume resources) | `POST /allocations` | ✅ |
| Get allocations per consumer (server) | `GET /allocations/{consumer_uuid}` | ✅ |
| Delete an allocation | `DELETE /allocations/{consumer_uuid}` | ✅ |
| Allocation candidates | `GET /allocation_candidates` | ✅ |
| Reshaper (atomic inventory + allocation move) | `POST /reshaper` | ✅ |
| Select a destination host | `POST /scheduler/select_destination` | ✅ |

#### Usages
| Feature | Endpoint | Status |
|---|---|:---:|
| Usage per project (quota-like) | `GET /usages` | ✅ |
| Usage per resource provider | `GET /resource_providers/{uuid}/usages` | ✅ |

### ❌ Missing Features (vs. the real Placement service)
| Feature | Notes |
|---|---|
| **Forbidden traits** | No negative trait filtering |
| **Microversion** header | No API versioning |
| **RBAC** at the placement level | Basic auth token only |

**Estimated Placement similarity: ~82%**

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
| **noVNC console** embedded (modal + detail tab + full page); needs a proxy on `NOVNC_BASE_URL` | ✅ |
| Live console log with 2-second auto-refresh | ✅ |
| Flavors, Key Pairs, Server Groups CRUD | ✅ |
| Hypervisors | ✅ |

#### Storage Pages
| Feature | Status |
|---|:---:|
| Volumes — table + create/extend/snapshot/detach modals | ✅ |
| Volume backup to object storage + restore from the detail page | ✅ |
| Snapshots, Volume Types (with AWS EBS presets) | ✅ |
| Object Storage: buckets, bucket types, objects | ✅ |
| File Store (NFS shares) | ✅ |

#### Networking Pages
| Feature | Status |
|---|:---:|
| Networks (network + subnet modal), Routers, Floating IPs | ✅ |
| Security Groups with a rules manager (inbound/outbound, AWS-style) | ✅ |
| Network detail tabs (Details/Subnets/Ports) | ✅ |
| Network topology graph + node inspector panel | ✅ |
| VPC peering, VPN gateways, BGP peers | ✅ |
| Load balancers (create, detail) | ✅ |
| DNS zones and records | ✅ |

#### Platform Pages
| Feature | Status |
|---|:---:|
| Stacks and scaling groups | ✅ |
| Telemetry, alarms, billing, event logs | ✅ |
| Secrets (key manager) | ✅ |
| Kubernetes clusters, containers, databases | ✅ |
| Queues (messaging), reservations | ✅ |
| High availability (protected VMs, hosts) | ✅ |

#### Admin & Overview
| Feature | Status |
|---|:---:|
| Overview dashboard: stat cards, allocation charts, quota progress bars | ✅ |
| API Access panel (endpoint catalog from the Identity service catalog) | ✅ |
| Admin: Projects / Users / Access Keys (tabs) | ✅ |
| Role management with an inline role selector, role assignments page | ✅ |
| WebSocket live events → auto-refresh of the instances & volumes tables | ✅ |

### ❌ Missing Features (vs. the real CloudX Dashboard)
| Feature | Notes |
|---|---|
| **Built-in noVNC proxy** | The console pages expect a proxy at `NOVNC_BASE_URL`; nothing in the packages starts one |
| **Trunks, QoS policies, subnet pools** | API and CLI only, no dedicated page |

**Estimated CloudX Dashboard similarity: ~90%**

---

## 9. Other Services

These services exist in the repository. Each is a partial re-implementation, so none is 1:1 with its counterpart.
The linked page for each says what is and is not covered.

| Cloud service | cloudx service | What it covers | What it lacks |
|---|---|---|---|
| **Heat** | `orchestration-api` | Stacks (create, update, preview, delete), resources, events, outputs, template; auto-scaling groups with scale-out and scale-in; docs/orchestration-vpc-bgp.md | Not 1:1; stacks run in a thread of the API process, so a restart mid-run leaves the stack `*_IN_PROGRESS` |
| **Swift** | `object-store-api` | Buckets, objects, bucket types; `s3` or `filesystem` driver | Not 1:1; the API is its own, not Swift's |
| **Barbican** | `key-manager-api` | Secrets and secret containers, stored encrypted with a master key | No orders or ACLs; volumes are not encrypted with it yet |
| **Octavia** | `load-balancer-api`, `lb-worker` | Load balancers, listeners, pools, members, health monitors, L7 policies and rules, certificates; HAProxy for L7 and OVN for L4 | Not 1:1 with Octavia's API |
| **Ceilometer / Aodh** | `telemetry-api`, `telemetry-worker` | Per-VM metrics from libvirt, alarms, rollups, retention; docs/telemetry-billing-events.md | Not 1:1 |
| **CloudKitty** | `billing-api`, `billing-worker` | Rating sheets, usage records, invoices (PDF/HTML); docs/telemetry-billing-events.md | Not 1:1 |
| **Panko** | `event-api`, `event-worker` | Event log, webhook subscriptions with HMAC and retry; docs/telemetry-billing-events.md | Not 1:1 |
| **Magnum** | `k8s-engine-api` | K3s clusters in CloudX VMs, not 1:1; see docs/k8s-engine.md | |
| **Designate** | `dns-api`, `dns-worker` | BIND9 zones, recordsets, secondaries and automatic names, not 1:1; see docs/dns.md | |
| **Manila** | `file-store-api` | NFSv4 shares via NFS-Ganesha, access rules, extend; no snapshots, quotas or CIFS, not 1:1; see docs/file-store.md | |
| **Zun** | `container-api` | Single containers with local storage, hardened by default; no block volumes or exec, not 1:1; see docs/container.md | |
| **Masakari** | `ha-api`, `ha-watchdog` | Heartbeat host monitor, iSCSI fencing, evacuate and a self-fencing watchdog; no IPMI/STONITH, ha-api is a single point of failure unless you run the second instance, not 1:1; see docs/ha.md | |
| **Trove** | `database-api` | PostgreSQL and MariaDB on a VM or container, crash-consistent volume backup and restore; no guest agent, replication or PITR, not 1:1; see docs/database.md | |
| **Blazar** | `reservation-api` | Time-boxed flavor leases admitted against Placement VCPU / RAM; a ledger only, not enforced against compute, not 1:1; see docs/reservation.md | |
| **Zaqar** | `messaging-api` | Queues with post, claim and delete (at-least-once) in PostgreSQL; no topics, subscriptions or webhooks, not 1:1; see docs/messaging.md | |

### Not Implemented

| Cloud service | Function | Notes |
|---|---|---|
| **Ironic** | Bare-metal provisioning | VMs only |
| **Senlin** | Clustering service | Only the scaling groups in `orchestration-api` |

---

## 📈 Percentage Comparison Chart

```
Service            Estimate
─────────────────────────────────────────────
Identity           ███████████████░░░░░  ~75%
Compute            █████████████████░░░  ~85%
Networking         ████████████████░░░░  ~80%
Image              ███████████████▌░░░░  ~78%
Block Storage      ████████████████▌░░░  ~82%
Metadata API       ██████████████████▌░  ~92%
Placement          ████████████████▌░░░  ~82%
CloudX Dashboard   ██████████████████░░  ~90%
```

---

## 📝 Assessment Methodology

The similarity percentage is calculated along three dimensions:

1. **API endpoint coverage** — what percentage of the main CloudX endpoints are implemented
2. **Behavioural fidelity** — whether the business logic behaves the same (response format, error codes, lifecycle states)
3. **Authentic backend** — whether real components are used (libvirt, OVN, LVM, Ceph) rather than mocks/simulations

> **Note:** The percentages are **qualitative estimates**, not a formal measurement. Some features are deliberately
> excluded (FWaaS, ephemeral disks, and so on), not because they cannot be built, but because they are outside the scope of this project.
> CloudX has hundreds of sub-features per service; cloudx focuses on the **operational happy path** that is enough
> to run real cloud infrastructure on a handful of nodes.
>
> The endpoint tables list what the code exposes. `scripts/check_feature_comparison.py` (it runs in the test suite)
> fails when a table names an endpoint that is not a route in `services/*/app`. It does not show that each endpoint
> works in every deployment; a route can exist and still be only lightly tested. The roadmap and release notes
> (`docs/releases/`) say which features were exercised on a lab.

### Deliberately Excluded Features (by design)
| Feature | Reason |
|---|---|
| Ephemeral disk | Volume-first architecture — every disk is a persistent volume |
| FWaaS | Security groups are enough |
| Multi-store image backend | File + MinIO is enough |
