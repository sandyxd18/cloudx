# 📊 Perbandingan Fitur: cloudx vs Standar Private Cloud

> Dokumen ini memetakan fitur yang sudah diimplementasikan di **cloudx private cloud lab**
> dibandingkan dengan spesifikasi cloud standar, beserta estimasi persentase kemiripan.
>
> **Diperbarui:** Agustus 2026 — mencakup Fase 1–4 (paritas API base services) dan
> Fase 5 (production readiness: multi-node, HA, backup, monitoring, internal TLS,
> Dashboard AWS Cloudscape).

---

## 🔢 Ringkasan Keseluruhan

| Service cloudx | Setara CloudX | Awal | Setelah F1–F4 | Setelah F5 |
|---|---|:---:|:---:|:---:|
| `auth-api` | **Identity** | 0% | ~93% | **~95%** |
| `compute-api` + `compute-worker` | **Compute** | ~45% | ~94% | **~95%** |
| `networking-api` | **Networking** | ~55% | ~88% | **~90%** |
| `image-api` | **Image** | ~40% | ~90% | **~92%** |
| `block-store-api` | **Block Storage** | ~35% | ~93% | **~95%** |
| `metadata-api` | **Metadata API** | ~75% | ~92% | **~92%** |
| `placement-api` | **Placement** | ~15% | ~92% | **~93%** |
| `dashboard` | **CloudX Dashboard** | 0% | ~85% | **~90%** |
| *(tidak ada)* | **Heat** | 0% | 0% | 0% |
| *(tidak ada)* | **Swift** | 0% | 0% | 0% (MinIO sebagai pengganti) |
| *(tidak ada)* | **Ironic** | 0% | 0% | 0% |

**Rata-rata keseluruhan (service yang diimplementasikan): ~92%**
*(vs ~72% sebelum Fase 1–4 dimulai)*

### Tambahan Fase 5 — Production Readiness (lintas service)

| Area | Fitur | Status |
|---|---|:---:|
| Infrastruktur | Deployment multi-node adaptif (Control Plane / Compute / Storage) | ✅ |
| Infrastruktur | High Availability: RabbitMQ cluster + PostgreSQL streaming replication | ✅ |
| Infrastruktur | HAProxy multi-backend round-robin + Keepalived VIP | ✅ |
| Backup | Automated DB backup ke MinIO (cron 23:00 Asia/Jakarta, retention tiered) | ✅ |
| Backup | Volume backup penuh ke object storage + restore (`POST /volumes/{id}/backup`) | ✅ |
| Observability | Prometheus (retention 15 hari) + Grafana dashboards ter-provision | ✅ |
| Observability | Endpoint `/metrics` di semua API services + alert rules | ✅ |
| Security | Internal TLS PKI (`services/certs/generate-certs.sh`, antar-service saja) | ✅ |
| Database | Alembic migration scaffold (baseline stamp, additive-first) | ✅ |
| CloudX Dashboard | UI dirancang ulang ala AWS Console dengan Cloudscape Design System | ✅ |

---

## 1. Identity (Identity) — `auth-api`

### ✅ Fitur yang Sudah Ada

#### Token & Autentikasi
| Fitur | Endpoint | Status |
|---|---|:---:|
| Login (username + password + project) | `POST /auth/tokens` | ✅ |
| Validasi token | `GET /auth/tokens` | ✅ |
| Logout (revoke token) | `DELETE /auth/tokens` | ✅ |
| JWT-based token (stateless) | Internal | ✅ |
| Token expiry (configurable) | `AUTH_TOKEN_EXPIRE_HOURS` | ✅ |

#### Project Management
| Fitur | Endpoint | Status |
|---|---|:---:|
| Buat project | `POST /projects` | ✅ |
| List project | `GET /projects` | ✅ |
| Show project | `GET /projects/{ref}` | ✅ |
| Delete project | `DELETE /projects/{ref}` | ✅ |
| Default project `admin` auto-bootstrap | Startup | ✅ |

#### User Management
| Fitur | Endpoint | Status |
|---|---|:---:|
| Buat user | `POST /users` | ✅ |
| List user | `GET /users` | ✅ |
| Show user | `GET /users/{ref}` | ✅ |
| Delete user | `DELETE /users/{ref}` | ✅ |
| Role assignment (admin/member/reader) | Terintegrasi | ✅ |
| Default admin user auto-bootstrap | Startup | ✅ |

#### Group Management
| Fitur | Endpoint | Status |
|---|---|:---:|
| Buat group | `POST /groups` | ✅ |
| List / Show / Delete group | `GET\|DELETE /groups` | ✅ |
| Add / Remove user dari group | `POST\|DELETE /groups/{ref}/users` | ✅ |

#### Access Key (AWS-style CLI Auth)
| Fitur | Endpoint | Status |
|---|---|:---:|
| Buat access key | `POST /access-keys` | ✅ |
| List access key | `GET /access-keys` | ✅ |
| Delete access key | `DELETE /access-keys/{key_id}` | ✅ |
| Validasi access key | `POST /auth/access-key` | ✅ |

### ❌ Fitur yang Belum Ada (Identity Asli)
| Fitur | Keterangan |
|---|---|
| **Service Catalog** | Tidak ada endpoint discovery catalog |
| **Domain** (multi-domain hierarchy) | Hanya satu domain |
| **Scope token** (domain/system scope) | Hanya project scope |
| **Federation / SAML / OAuth2** | Tidak ada SSO |
| **Application credentials** | Mirip access key, tapi tidak ada secret rotation |
| **Multi-factor authentication** | Tidak ada |
| **Token audit / Fernet token** | JWT saja, tidak ada Fernet |
| **Endpoint registration** | Tidak ada service catalog API |
| **Policy (oslo.policy)** | RBAC sederhana, bukan oslo.policy |
| **Trust** (delegate token) | Tidak ada |

**Estimasi kemiripan Identity: ~65%**

---

## 2. Compute (Compute API) — `compute-api` + `compute-worker`

### ✅ Fitur yang Sudah Ada

#### Server Lifecycle
| Fitur | Endpoint | Status |
|---|---|:---:|
| Buat server (boot VM) | `POST /servers` | ✅ |
| List server | `GET /servers` | ✅ |
| Show server detail | `GET /servers/{ref}` | ✅ |
| Start server | `POST /servers/{ref}/start` | ✅ |
| Stop server (graceful shutdown) | `POST /servers/{ref}/stop` | ✅ |
| Reboot server (soft & hard) | `POST /servers/{ref}/reboot` | ✅ |
| **Pause** (hypervisor-level suspend) | `POST /servers/{ref}/pause` | ✅ |
| **Resume** dari pause | `POST /servers/{ref}/resume` | ✅ |
| **Suspend** (managed save ke disk) | `POST /servers/{ref}/suspend` | ✅ |
| **Unsuspend** (restore dari managed save) | `POST /servers/{ref}/unsuspend` | ✅ |
| **Resize** (live vCPU + RAM hotplug) | `POST /servers/{ref}/resize` | ✅ |
| **Rebuild** (ganti root disk dari image baru) | `POST /servers/{ref}/rebuild` | ✅ |
| **Rescue** (boot dari rescue image) | `POST /servers/{ref}/rescue` | ✅ |
| **Unrescue** (kembali ke root disk) | `POST /servers/{ref}/unrescue` | ✅ |
| Delete server | `DELETE /servers/{ref}` | ✅ |
| Delete + keep root volume | `DELETE /servers/{ref}?keep_root_volume=true` | ✅ |
| Snapshot server ke image | `POST /servers/{ref}/snapshots` | ✅ |

#### Server Console & Diagnostics
| Fitur | Endpoint | Status |
|---|---|:---:|
| Serial console (virsh console) | `GET /servers/{ref}/console` | ✅ |
| Console URL (VNC info) | `GET /servers/{ref}/console-url` | ✅ |
| Console log (PTY output) | `GET /servers/{ref}/logs` | ✅ |
| SSH info (IP + keypair) | `GET /servers/{ref}/ssh-info` | ✅ |
| Password injection via QEMU GA | `POST /servers/{ref}/password` | ✅ |

#### Metadata & User Data
| Fitur | Endpoint | Status |
|---|---|:---:|
| Get server metadata | `GET /servers/{ref}/metadata` | ✅ |
| Get user-data | `GET /servers/{ref}/user-data` | ✅ |
| Set user-data | `PUT /servers/{ref}/user-data` | ✅ |

#### Flavor
| Fitur | Endpoint | Status |
|---|---|:---:|
| Buat flavor (vCPU, RAM, Disk) | `POST /flavors` | ✅ |
| List / Show / Delete flavor | `GET\|DELETE /flavors` | ✅ |
| Default flavors (nano, micro, tiny, small, medium) | Bootstrap | ✅ |

#### Keypair
| Fitur | Endpoint | Status |
|---|---|:---:|
| Generate keypair (RSA 2048) | `POST /keypairs` | ✅ |
| Import keypair (public key) | `POST /keypairs/import` | ✅ |
| List / Show / Delete keypair | `GET\|DELETE /keypairs` | ✅ |

#### Server Groups
| Fitur | Endpoint | Status |
|---|---|:---:|
| Buat server group | `POST /server-groups` | ✅ |
| Policy: affinity / anti-affinity | Terintegrasi | ✅ |
| List / Show / Delete server group | `GET\|DELETE /server-groups` | ✅ |
| Enforcement affinity saat create server | Terintegrasi di `POST /servers` | ✅ |

#### Network & Volume Hotplug
| Fitur | Endpoint | Status |
|---|---|:---:|
| Attach port ke server (multi-NIC) | `POST /servers/{ref}/ports` | ✅ |
| Detach port dari server | `DELETE /servers/{ref}/ports/{port_ref}` | ✅ |
| Attach volume (LVM/Ceph) ke server | `POST /servers/{ref}/volumes/attach` | ✅ |
| Detach volume dari server | `POST /servers/{ref}/volumes/detach` | ✅ |

#### Host
| Fitur | Endpoint | Status |
|---|---|:---:|
| Host check (KVM, libvirt, OVS) | `GET /host/check` | ✅ |
| Host resource info | `GET /host` | ✅ |

#### Backend Compute (compute-worker)
| Fitur | Status |
|---|:---:|
| Provisioning via **libvirt/QEMU/KVM** | ✅ |
| Boot dari **LVM block device** (`/dev/VG/LV`) | ✅ |
| Boot dari **Ceph RBD** (network disk) | ✅ |
| Config drive NoCloud ISO untuk cloud-init | ✅ |
| Inject SSH public key via cloud-init | ✅ |
| Tap interface + OVS binding | ✅ |
| OVN port binding per VM | ✅ |
| libvirt XML auto-detect: block / network / file | ✅ |

### ❌ Fitur yang Belum Ada (Compute Asli)
| Fitur | Keterangan |
|---|---|
| **Live Migration** | Single-node, tidak ada live migration |
| **Evacuation** | Single-node only |
| **Aggregates / Availability Zones** | Sengaja dikecualikan |
| **Ephemeral disk terpisah** | Volume-first, tidak ada ephemeral disk |
| **VNC/SPICE console via noVNC** | Hanya serial console (VNC port ada tapi belum proxied) |
| **Multi-node scheduling** | Single-node only |
| **Config drive format vfat** | Hanya ISO9660 |
| **Trusted image certificates** | Tidak ada |
| **Cold migration / resize confirm/revert** | Resize hanya live (hotplug), tanpa confirm/revert |
| **Shelve / Unshelve** | Tidak ada |
| **Lock / Unlock server** | Tidak ada |
| **Server tags** | Tidak ada |
| **Trigger crash dump** | Tidak ada |

**Estimasi kemiripan Compute: ~80%**

---

## 3. Networking (Networking API) — `networking-api`

### ✅ Fitur yang Sudah Ada

#### Network & Subnet
| Fitur | Endpoint | Status |
|---|---|:---:|
| Buat network (internal/tenant) | `POST /networks` | ✅ |
| Buat external/provider network | `POST /networks` (`external=true`) | ✅ |
| List / Show / Delete network | `GET\|DELETE /networks` | ✅ |
| Buat subnet dengan CIDR, gateway, DNS | `POST /subnets` | ✅ |
| DHCP via OVN DHCP options | Terintegrasi | ✅ |
| IPAM (alokasi IP otomatis) | Terintegrasi di port | ✅ |
| List / Show / Delete subnet | `GET\|DELETE /subnets` | ✅ |
| `no-dhcp` option untuk provider subnet | Terintegrasi | ✅ |

#### Router & L3
| Fitur | Endpoint | Status |
|---|---|:---:|
| Buat router | `POST /routers` | ✅ |
| Set gateway (external network) | `POST /routers/{ref}/gateway` | ✅ |
| Add interface (subnet) | `POST /routers/{ref}/interfaces` | ✅ |
| Remove interface | `DELETE /routers/{ref}/interfaces/{subnet}` | ✅ |
| List / Show / Delete router | `GET\|DELETE /routers` | ✅ |
| OVN Logical Router | Backend OVN | ✅ |
| Gateway chassis binding (OVN) | Backend OVN | ✅ |

#### Port
| Fitur | Endpoint | Status |
|---|---|:---:|
| Buat port (manual) | `POST /ports` | ✅ |
| List / Show / Delete port | `GET\|DELETE /ports` | ✅ |
| Set security group pada port | `POST /ports/{ref}/security-groups` | ✅ |
| Clear security groups | `POST /ports/{ref}/security-groups/clear` | ✅ |
| Enable / Disable port security | `POST /ports/{ref}/security/enable\|disable` | ✅ |
| Inspeksi OVN state port | `GET /ports/{ref}/ovn` | ✅ |
| MAC address otomatis | Terintegrasi | ✅ |

#### Floating IP
| Fitur | Endpoint | Status |
|---|---|:---:|
| Buat floating IP | `POST /floating-ips` | ✅ |
| Associate FIP ke server/port | `POST /floating-ips/{ref}/associate` | ✅ |
| Disassociate FIP | `POST /floating-ips/{ref}/disassociate` | ✅ |
| Delete FIP | `DELETE /floating-ips/{ref}` | ✅ |
| List / Show FIP | `GET /floating-ips` | ✅ |
| OVN DNAT+SNAT NAT rule | Backend OVN | ✅ |

#### Security Group
| Fitur | Endpoint | Status |
|---|---|:---:|
| Buat security group | `POST /security-groups` | ✅ |
| List / Show / Delete SG | `GET\|DELETE /security-groups` | ✅ |
| Tambah rule (ingress/egress, TCP/UDP/ICMP/any) | `POST /security-groups/{ref}/rules` | ✅ |
| Delete rule | `DELETE /security-group-rules/{id}` | ✅ |
| List rules per SG | `GET /security-groups/{ref}/rules` | ✅ |
| Default SG otomatis per project | Bootstrap | ✅ |
| OVN Port Group + ACL translasi | Backend OVN | ✅ |
| Remote group (SG referencing SG lain) | Terintegrasi | ✅ |
| Inspeksi OVN state SG | `GET /security-groups/{ref}/ovn` | ✅ |
| Port-level security group enforcement | OVN ACL | ✅ |

#### Provider Network (OVN Localnet)
| Fitur | Status |
|---|:---:|
| OVN `localnet` port ke `br-ex` | ✅ |
| Bridge mapping `physnet1:br-ex` | ✅ |
| ARP proxy di OVN | ✅ |
| Gateway chassis binding | ✅ |

#### OVN Diagnostics
| Fitur | Endpoint | Status |
|---|---|:---:|
| Show seluruh OVN topology | `GET /ovn/show` | ✅ |

### ❌ Fitur yang Belum Ada (Networking Asli)
| Fitur | Keterangan |
|---|---|
| **QoS (Quality of Service)** | Tidak ada bandwidth limiting |
| **Load Balancer (Octavia)** | Sengaja dikecualikan |
| **VPN as a Service** | Sengaja dikecualikan |
| **Firewall as a Service (FWaaS)** | Sengaja dikecualikan |
| **BGP Dynamic Routing** | Sengaja dikecualikan |
| **Network Segment** | Tidak ada VLAN/VXLAN segment manual |
| **Trunk ports (VLAN-in-VLAN)** | Tidak ada |
| **IPv6** | Hanya IPv4 |
| **DNS Integration (Designate)** | Tidak ada |
| **RBAC policies per network** | Multi-tenancy by project, bukan RBAC network |
| **Port forwarding** | Tidak ada DNAT per-port |
| **Address scopes** | Tidak ada |
| **Shared networks** | Tidak ada network sharing antar project |
| **Subnet pools / address pools** | IPAM manual per subnet |

**Estimasi kemiripan Networking: ~65%**

---

## 4. Image (Image API) — `image-api`

### ✅ Fitur yang Sudah Ada

| Fitur | Endpoint | Status |
|---|---|:---:|
| Upload image (multipart file) | `POST /images` | ✅ |
| Import image dari path lokal | `POST /images/import-path` | ✅ |
| List images | `GET /images` | ✅ |
| Show image detail | `GET /images/{ref}` | ✅ |
| Download image | `GET /images/{ref}/download` | ✅ |
| Delete image | `DELETE /images/{ref}` | ✅ |
| Update image metadata | `PATCH /images/{ref}` | ✅ |
| **Deactivate image** | `POST /images/{ref}/deactivate` | ✅ |
| **Reactivate image** | `POST /images/{ref}/reactivate` | ✅ |
| **Visibility: public / private** | Terintegrasi + project scoping | ✅ |
| Format: `qcow2`, `raw`, `iso` | Terintegrasi | ✅ |
| Backend: **file lokal** | Default | ✅ |
| Backend: **MinIO** (S3-compatible) | `IMAGE_STORAGE_BACKEND=minio` | ✅ |
| Metadata image (name, disk_format, size, checksum) | Terintegrasi | ✅ |
| Project scoping (private image per project) | Terintegrasi | ✅ |

### ❌ Fitur yang Belum Ada (Image Asli)
| Fitur | Keterangan |
|---|---|
| **Image member** (share image antar project) | Tidak ada explicit sharing |
| **Image tags** | Tidak ada tagging |
| **Image signing & verification** | Tidak ada Barbican integration |
| **Import tasks** (async via task API) | Upload synchronous |
| **Multi-store backend** (Ceph, Swift, HTTP) | Hanya file + MinIO (sengaja dibatasi) |
| **Image encryption** | Tidak ada |
| **Pagination & advanced filtering** | Filter dasar saja |
| **Image caching** (dedicated cache service) | Cache hanya di compute-worker |
| **Image conversion** (format auto-convert) | Tidak ada task-based conversion |

**Estimasi kemiripan Image: ~70%**

---

## 5. Block Storage (Block Storage) — `block-store-api`

### ✅ Fitur yang Sudah Ada

#### Volume Lifecycle
| Fitur | Endpoint | Status |
|---|---|:---:|
| Buat volume kosong | `POST /volumes` | ✅ |
| Buat volume dari image (boot volume) | `POST /volumes` (`image=...`) | ✅ |
| Buat volume dari snapshot | `POST /volumes` (`snapshot=...`) | ✅ |
| Buat volume dari volume lain (clone) | `POST /volumes` (`source_volume=...`) | ✅ |
| List / Show volume | `GET /volumes` | ✅ |
| Delete volume | `DELETE /volumes/{ref}` | ✅ |
| Attach volume ke server | `POST /volumes/{ref}/attach` | ✅ |
| Detach volume dari server | `POST /volumes/{ref}/detach` | ✅ |
| Force detach | `POST /volumes/{ref}/force-detach` | ✅ |
| Detach semua volume dari server | `POST /servers/{id}/volumes/detach-all` | ✅ |
| **Extend volume** (resize) | `POST /volumes/{ref}/extend` | ✅ |
| **Backup volume** (snapshot as backup) | `POST /volumes/{ref}/backup` | ✅ |

#### Volume Types — AWS EBS-style Performance Profiles
| Fitur | Endpoint | Status |
|---|---|:---:|
| List volume types (alphabetical) | `GET /volume-types` | ✅ |
| Buat volume type (admin) | `POST /volume-types` | ✅ |
| Show volume type | `GET /volume-types/{ref}` | ✅ |
| Update volume type (mutable fields) | `PATCH /volume-types/{ref}` | ✅ |
| Delete volume type (admin) | `DELETE /volume-types/{ref}` | ✅ |
| **Proteksi hapus jika type sedang dipakai** | Embedded | ✅ |
| **Tidak ada auto-bootstrap** — admin wajib buat sendiri | Design | ✅ |
| Field `disk_type`: `sata_ssd`/`sas_ssd`/`nvme_ssd`/`sata_hdd`/`sas_hdd` | Schema | ✅ |
| Field `max_iops` (informational, tidak di-enforce) | Schema | ✅ |
| Field `max_throughput` MB/s (informational) | Schema | ✅ |
| Field `backend`: `lvm` atau `ceph` (infrastruktur) | Schema | ✅ |

#### Storage Backend
| Fitur | Status |
|---|:---:|
| **Backend LVM** (block device `/dev/VG/LV`) | ✅ |
| **Auto-create VG** dari `LVM_PV_NAME` saat startup | ✅ |
| **Backend Ceph RBD** (network disk `rbd:pool/image`) | ✅ |
| Kedua backend bisa aktif bersamaan | ✅ |
| `STORAGE_LVM_DRIVER` + `STORAGE_CEPH_RBD_DRIVER` toggle | ✅ |
| Image-to-LVM: `qemu-img convert -O raw ... /dev/VG/LV` | ✅ |
| Image-to-Ceph: `qemu-img convert ... rbd:pool/image` | ✅ |
| LVM snapshot (COW, 20% size) | ✅ |
| Ceph RBD snapshot | ✅ |

#### Snapshot
| Fitur | Endpoint | Status |
|---|---|:---:|
| Buat snapshot dari volume | `POST /volumes/{ref}/snapshots` | ✅ |
| Buat snapshot (standalone) | `POST /snapshots` | ✅ |
| List / Show / Delete snapshot | `GET\|DELETE /snapshots` | ✅ |

#### Boot-from-Volume
| Fitur | Status |
|---|:---:|
| Boot VM dari LVM root volume | ✅ |
| Boot VM dari Ceph RBD root volume | ✅ |
| Volume-first architecture (semua VM boot dari volume) | ✅ |
| Delete VM + keep root volume | ✅ |

### ❌ Fitur yang Belum Ada (Block Storage Asli)
| Fitur | Keterangan |
|---|---|
| **Volume encryption** | Tidak ada Barbican integration |
| **Volume migration** (antar backend) | Tidak ada online migration |
| **Volume replication** | Tidak ada HA replication |
| **Consistency groups** | Tidak ada |
| **Generic volume groups** | Tidak ada |
| **Quota management** | Tidak ada per-project volume quota |
| **Multi-attach volume** | 1 server per volume |
| **iSCSI / NFS / FC backend** | Hanya LVM + Ceph |
| **LVM thin provisioning** | LVM thick saja |
| **QoS enforcement** | max_iops/max_throughput informational only, tidak ada cgroups/rate limit |

**Estimasi kemiripan Block Storage: ~75%**

---

## 6. Metadata API — `metadata-api`

### ✅ Fitur yang Sudah Ada

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
| HMAC signing key via `METADATA_HMAC_KEY` (opsional) | ✅ |

#### Lookup Mechanism
| Fitur | Status |
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

### ❌ Fitur yang Belum Ada
| Fitur | Keterangan |
|---|---|
| **Dynamic vendor_data2** via webhook | `dynamic` section kosong |
| **X.509 instance identity cert** | Hanya HMAC, tidak ada PKI |
| **`launch-index`** multi-instance | Selalu 0 |

**Estimasi kemiripan Metadata API: ~92%**

---

## 7. Placement — `placement-api`

### ✅ Fitur yang Sudah Ada

#### Resource Provider
| Fitur | Endpoint | Status |
|---|---|:---:|
| Buat resource provider | `POST /resource_providers` | ✅ |
| List / Show resource provider | `GET /resource_providers` | ✅ |
| Delete resource provider | `DELETE /resource_providers/{uuid}` | ✅ |

#### Inventory
| Fitur | Endpoint | Status |
|---|---|:---:|
| Set inventory (VCPU, MEMORY_MB, DISK_GB) | `PUT /resource_providers/{uuid}/inventories` | ✅ |
| Get inventory | `GET /resource_providers/{uuid}/inventories` | ✅ |

#### Traits
| Fitur | Endpoint | Status |
|---|---|:---:|
| List traits | `GET /traits` | ✅ |
| Set traits pada provider | `PUT /resource_providers/{uuid}/traits` | ✅ |
| Get traits provider | `GET /resource_providers/{uuid}/traits` | ✅ |

#### Allocations
| Fitur | Endpoint | Status |
|---|---|:---:|
| Buat alokasi (consume resources) | `POST /allocations` | ✅ |
| Get alokasi per consumer (server) | `GET /allocations/{consumer_uuid}` | ✅ |
| Delete alokasi | `DELETE /allocations/{consumer_uuid}` | ✅ |

#### Usages
| Fitur | Endpoint | Status |
|---|---|:---:|
| Usage per project (quota-like) | `GET /usages` | ✅ |
| Usage per resource provider | `GET /resource_providers/{uuid}/usages` | ✅ |

### ❌ Fitur yang Belum Ada (Placement Asli)
| Fitur | Keterangan |
|---|---|
| **Nested Resource Providers** | Tidak ada hierarki provider |
| **Allocation candidates** (`GET /allocation_candidates`) | Tidak ada scheduling hints |
| **Reshaper** (atomic inventory+allocation) | Tidak ada |
| **Aggregate association** | Tidak ada aggregates |
| **Forbidden traits** | Tidak ada negative trait filtering |
| **Microversion** header | Tidak ada versioning API |
| **RBAC** di level placement | Hanya auth token dasar |

**Estimasi kemiripan Placement: ~70%**

---

## 8. CloudX Dashboard (Web Dashboard) — `dashboard`

### ✅ Fitur yang Sudah Ada

#### Shell & Navigasi ala AWS Console (Cloudscape Design System)
| Fitur | Status |
|---|:---:|
| **TopNavigation bar** — identity, global search jump-to-service | ✅ |
| **Dark / Light mode toggle** di header (persisten via localStorage) | ✅ |
| Project switcher (multi-project scope) di utilities menu | ✅ |
| User menu (API access, access keys, roles, sign out) | ✅ |
| AppLayout + SideNavigation per service group + breadcrumbs otomatis | ✅ |
| Notification Flashbar (toast lama dimigrasi penuh) | ✅ |

#### Compute Pages
| Fitur | Status |
|---|:---:|
| Instances — Cloudscape Table multi-select, filter, pagination | ✅ |
| Bulk instance state actions (start/stop/reboot/delete) | ✅ |
| Actions dropdown: pause/resume/suspend/rescue/shelve/lock/resize confirm-revert | ✅ |
| Launch instance modal (flavor/image/network/keypair select) | ✅ |
| Instance detail tabs: Details / Networking / Storage / Console | ✅ |
| **noVNC console** embedded (modal + tab detail + halaman penuh) | ✅ |
| Live console log dengan auto-refresh 2 detik | ✅ |
| Flavors, Key Pairs, Server Groups CRUD | ✅ |

#### Storage Pages
| Fitur | Status |
|---|:---:|
| Volumes — table + create/extend/snapshot/detach modals | ✅ |
| Volume backup ke object storage + restore dari detail page | ✅ |
| Snapshots, Volume Types (dengan preset AWS EBS) | ✅ |

#### Networking Pages
| Fitur | Status |
|---|:---:|
| Networks (network + subnet modal), Routers, Floating IPs | ✅ |
| Security Groups dengan rules manager (inbound/outbound ala AWS) | ✅ |
| Network detail tabs (Details/Subnets/Ports) | ✅ |
| Network topology graph (force-directed SVG) + node inspector panel | ✅ |

#### Admin & Overview
| Fitur | Status |
|---|:---:|
| Overview dashboard: stat cards, allocation charts, quota progress bars | ✅ |
| API Access panel (endpoint catalog dari Identity service catalog) | ✅ |
| Admin: Projects / Users / Access Keys (tabs) | ✅ |
| Role management dengan inline role selector | ✅ |
| WebSocket live events → auto-refresh tabel instances & volumes | ✅ |

### ❌ Fitur yang Belum Ada (CloudX Dashboard Asli)
| Fitur | Keterangan |
|---|---|
| **Heat / Orchestration UI** | Service Heat belum diimplementasikan |
| **Object Storage (Swift) UI** | MinIO hanya backend image/backup, bukan UI file manager |

**Estimasi kemiripan CloudX Dashboard: ~90%**
*(UI dirancang ulang dengan AWS Cloudscape Design System pada Fase 5)*

---

## 9. Service yang Sama Sekali Tidak Ada

| Layanan Cloud | Fungsi | Keterangan |
|---|---|---|
| **Heat** | Orchestration / IaC (template stack) | Tidak ada template engine |
| **Swift** | Object storage | MinIO digunakan sebagai pengganti untuk image backend |
| **Ironic** | Bare metal provisioning | Single-node VM only |
| **Magnum** | Container orchestration (Kubernetes-as-a-Service) | Tidak ada |
| **Octavia** | Load balancer as a Service | Sengaja dikecualikan |
| **Barbican** | Secret / certificate management | Tidak ada |
| **Designate** | DNS as a Service | Tidak ada |
| **Manila** | Shared file system (NFS, CIFS) | Tidak ada |
| **Zun** | Container service (Docker) | Tidak ada |
| **Masakari** | Instance HA / failover | Single-node |
| **Blazar** | Resource reservation | Tidak ada |
| **Senlin** | Clustering service | Tidak ada |

---

## 📈 Grafik Perbandingan Persentase

```
Service         Awal   Setelah F1–F4   Setelah F5
──────────────────────────────────────────────────────
Identity           0%  ██████████████  ~93%  ██████████████▌  ~95%
Compute              45%  ██████████████  ~94%  ██████████████▌  ~95%
Networking           55%  █████████████   ~88%  █████████████▌   ~90%
Image            40%  █████████████   ~90%  █████████████▌   ~92%
Block Storage            35%  █████████████   ~93%  █████████████▌   ~95%
Metadata          75%  █████████████   ~92%  █████████████    ~92%
Placement         15%  █████████████   ~92%  █████████████▌   ~93%
CloudX Dashboard            0%  ████████████    ~85%  █████████████    ~90% (*)

(*) CloudX Dashboard dirancang ulang dengan AWS Cloudscape Design System pada Fase 5.
```

---

## 📝 Metodologi Penilaian

Persentase kemiripan dihitung berdasarkan tiga dimensi:

1. **Coverage API endpoint** — berapa persen endpoint utama CloudX yang sudah diimplementasikan
2. **Fidelitas perilaku** — apakah logika bisnis berperilaku sama (format response, error codes, lifecycle states)
3. **Backend otentik** — apakah menggunakan komponen nyata (libvirt, OVN, LVM, Ceph) vs mock/simulasi

> **Catatan:** Persentase bersifat **estimasi kualitatif**, bukan pengukuran formal. Beberapa fitur sengaja
> dikecualikan (LBaaS, multi-AZ, dll.) bukan karena tidak bisa dibuat, tetapi karena di luar scope lab ini.
> CloudX memiliki ratusan sub-fitur per service; cloudx fokus pada **happy path operational** yang cukup
> untuk menjalankan infrastruktur cloud nyata di single-node.

### Fitur yang Sengaja Dikecualikan (by design)
| Fitur | Alasan |
|---|---|
| Aggregates / Availability Zones | Single-node, tidak relevan |
| Ephemeral disk | Volume-first architecture — semua disk adalah persistent volume |
| Load Balancer (Octavia) | Kompleksitas tinggi, out of scope |
| VPN as a Service | Out of scope |
| FWaaS | Security group sudah cukup untuk lab |
| BGP Dynamic Routing | Out of scope |
| Multi-store image backend | File + MinIO sudah cukup |
