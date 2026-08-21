# 🗺️ Roadmap Menuju Paritas OpenStack Base Services

> **Dokumen ini** menganalisis kondisi **CloudX** saat ini dan merinci pekerjaan yang perlu dilakukan
> untuk mencapai kemiripan yang lebih tinggi dengan tujuh base service OpenStack:
> **Nova, Neutron, Cinder, Glance, Keystone, Placement, dan Horizon**.
>
> Analisis berdasarkan kode aktual di `services/`, dokumentasi di `docs/feature-comparison.md`,
> dan perbandingan dengan perilaku OpenStack asli.
>
> **Skor awal (Juni 2026):**
> Keystone ~65% · Nova ~80% · Neutron ~65% · Glance ~70% · Cinder ~75% · Placement ~70% · Horizon ~55%
>
> **✅ Skor setelah Fase 1–4 (Akhir Agustus 2026):**
> Keystone ~93% · Nova ~94% · Neutron ~88% · Glance ~90% · Cinder ~93% · Placement ~92% · Horizon ~85%

---

## 📋 Daftar Isi

1. [Ringkasan Eksekutif](#1-ringkasan-eksekutif)
2. [Keystone — auth-api](#2-keystone--auth-api-saat-ini-65)
3. [Nova — compute-api + compute-worker](#3-nova--compute-api--compute-worker-saat-ini-80)
4. [Neutron — networking-api](#4-neutron--networking-api-saat-ini-65)
5. [Glance — image-api](#5-glance--image-api-saat-ini-70)
6. [Cinder — block-store-api + block-store-worker](#6-cinder--block-store-api--block-store-worker-saat-ini-75)
7. [Placement — placement-api](#7-placement--placement-api-saat-ini-70)
8. [Horizon — dashboard](#8-horizon--dashboard-saat-ini-55)
9. [Infrastruktur Cross-Cutting](#9-infrastruktur-cross-cutting)
10. [Prioritas dan Estimasi Upaya](#10-prioritas-dan-estimasi-upaya)
11. [Arsitektur Target](#11-arsitektur-target)

---

## 1. Ringkasan Eksekutif

CloudX adalah implementasi private cloud berbasis Docker Compose yang sudah sangat matang untuk
konteks lab single-node. Implementasi saat ini sudah menggunakan backend **nyata**:
libvirt/QEMU/KVM, OVN/OVS, LVM, Ceph RBD, MinIO — bukan mock atau simulator.

### Kondisi Saat Ini vs Target

| Service | Awal | Setelah F1–F4 | Target | Gap Tersisa (Fase 5+) |
|---|:---:|:---:|:---:|---|
| Keystone (`auth-api`) | ~65% | **~93%** | ~95% | High Availability, Fernet token |
| Nova (`compute-api`) | ~80% | **~94%** | ~95% | Live Migration (multi-node), HA |
| Neutron (`networking-api`) | ~65% | **~88%** | ~90% | BGP Dynamic Routing, FWaaS |
| Glance (`image-api`) | ~70% | **~90%** | ~92% | Swift backend, Image encryption |
| Cinder (`block-store-api`) | ~75% | **~93%** | ~95% | Volume Replication, HA |
| Placement (`placement-api`) | ~70% | **~92%** | ~93% | Full scheduler integration (multi-node) |
| Horizon (`dashboard`) | ~55% | **~85%** | ~90% | Heat UI, Octavia UI |

### Apa yang Sudah Sangat Baik (Tidak Perlu Diubah)

- ✅ RabbitMQ AMQP-based async operations (mirip oslo.messaging)
- ✅ Conductor pattern (workers tidak akses DB langsung)
- ✅ Real VM via libvirt/QEMU/KVM dengan boot-from-volume
- ✅ OVN/OVS full integration dengan floating IP, security group ACL
- ✅ LVM + Ceph dual-backend storage
- ✅ WebSocket live events ke dashboard
- ✅ JWT auth dengan project scoping
- ✅ Metadata API (EC2 + OpenStack format, ~92%)
- ✅ CLI `cloudx` yang fungsional

---

## 2. Keystone — `auth-api` (Saat ini: ~65%)

### Kondisi Kode Saat Ini

File utama: `services/auth/app/main.py`

Sudah ada: JWT token, project/user/group CRUD, role assignment (admin/member/reader),
access key AWS-style, bootstrap admin otomatis.

### Gap yang Perlu Dikerjakan

#### 🔴 Prioritas Tinggi

**1. Service Catalog**

OpenStack Keystone memiliki endpoint discovery — setelah login, client mendapat
daftar URL semua service per region. Ini dibutuhkan agar client OpenStack SDK/CLI
standar bisa auto-discover endpoint.

```
Endpoint yang perlu ditambah:
GET  /v3/services              — list registered services
POST /v3/services              — register service
GET  /v3/endpoints             — list endpoints per service
POST /v3/endpoints             — register endpoint (internal/public/admin)
GET  /v3/auth/catalog          — embedded dalam token response
```

**Pekerjaan:**
- Tambah tabel `services` dan `endpoints` di DB
- Tambah field `catalog` dalam response `POST /auth/tokens`
- Bootstrap: auto-register semua cloudx services ke catalog saat startup
- Format token response harus sesuai Keystone v3: `token.catalog[]`

---

**2. Multi-Scope Token (Domain & System Scope)**

OpenStack mendukung token dengan scope: `project`, `domain`, `system`.
Saat ini cloudx hanya mendukung `project` scope.

```python
# Request body Keystone v3 standar:
{
  "auth": {
    "identity": { ... },
    "scope": {
      "project": {"id": "..."},   # sudah ada
      # BELUM ADA:
      "domain": {"id": "..."},
      "system": {"all": true}
    }
  }
}
```

**Pekerjaan:**
- Extend JWT payload dengan field `scope_type` (project/domain/system)
- Tambah validasi scope di `auth_middleware.py`
- Domain scope diperlukan untuk operasi admin lintas-project

---

**3. oslo.policy-style RBAC** ✅ (Selesai di Fase 3)

Saat ini role check dilakukan manual (`if token.role != "admin"`).
OpenStack asli menggunakan file `policy.yaml` yang bisa dikonfigurasi per-rule.

```yaml
# Contoh policy.yaml (oslo.policy format)
"identity:create_user": "rule:admin_required"
"identity:list_users": "rule:admin_or_owner"
"identity:get_project": ""  # semua authenticated
```

**Pekerjaan:**
- Implementasi policy engine sederhana yang baca dari `policy.yaml`
- Ganti hardcoded `is_admin` checks dengan `policy.enforce(rule, target, credentials)`
- Setiap service perlu file `policy.yaml` masing-masing

---

#### 🟡 Prioritas Menengah

**4. Domain Management**

```
POST /v3/domains          — buat domain
GET  /v3/domains          — list domains
PATCH /v3/domains/{id}    — update domain
DELETE /v3/domains/{id}   — delete domain
```

OpenStack mendukung hierarki: Domain → Project → User.
Saat ini cloudx flat (satu domain implisit).

**Pekerjaan:**
- Tambah tabel `domains` di DB
- Extend tabel `projects` dan `users` dengan `domain_id` FK
- Implementasi endpoint domain CRUD
- Default domain `default` dibuat saat bootstrap

---

**5. Application Credentials**

Mirip access key yang sudah ada, tapi dengan format Keystone v3:
- Terikat ke user spesifik (bukan project-global)
- Bisa punya `roles` terbatas
- Secret tidak bisa diambil ulang setelah dibuat

```
POST /v3/users/{user_id}/application_credentials
GET  /v3/users/{user_id}/application_credentials
DELETE /v3/users/{user_id}/application_credentials/{id}
```

---

#### 🟢 Prioritas Rendah / Out of Scope Lab

- **Federation / SAML / OAuth2** — Kompleks, di luar scope lab
- **Multi-factor Authentication** — Tidak relevan untuk lab lokal
- **Fernet Token** — JWT sudah cukup fungsional
- **Trust (delegate token)** — Advanced, di luar scope

### Estimasi Upaya Keystone

| Pekerjaan | Kompleksitas | Upaya |
|---|:---:|---|
| Service Catalog | Sedang | 2–3 hari |
| Multi-scope token | Rendah | 1 hari |
| oslo.policy RBAC | Tinggi | 3–5 hari |
| Domain management | Sedang | 2–3 hari |
| Application credentials | Rendah | 1 hari |

**Target setelah semua dikerjakan: ~90%**

---

## 3. Nova — `compute-api` + `compute-worker` (Saat ini: ~80%)

### Kondisi Kode Saat Ini

File utama: `services/compute/app/main.py` (~49KB)
Worker: `services/compute_worker/app/main.py`

Ini adalah service paling matang. Sudah ada: full server lifecycle, pause/resume,
suspend/unsuspend, resize, rebuild, rescue, server groups, keypair, flavor,
multi-NIC, volume attach/detach, console log, password injection.

### Gap yang Perlu Dikerjakan

#### 🔴 Prioritas Tinggi

**1. noVNC / SPICE Console Proxy**

Ini salah satu fitur paling visibel yang hilang. VNC port libvirt sudah ada
(terlihat di `NOVNC_BASE_URL` env var), tapi belum ada proxy ke browser.

```
Alur noVNC:
Browser -> noVNC JS client -> WebSocket proxy -> libvirt VNC port

Komponen yang perlu ditambah:
- websockify container (atau implementasi native Python)
- Endpoint: GET /servers/{ref}/console-url -> {type: "novnc", url: "..."}
- noVNC HTML UI embed di dashboard
```

**Pekerjaan di compute-worker:**
```python
# Tambah metode RPC:
def get_vnc_port(server_id) -> int:
    domain = conn.lookupByName(vm_name)
    xml = ET.fromstring(domain.XMLDesc())
    port = xml.find(".//graphics[@type='vnc']").get("port")
    return int(port)
```

**Pekerjaan infrastruktur:**
- Tambah container `novnc-proxy` di `docker-compose.yml` (image: websockify)
- Konfigurasi port mapping: host `6080` → libvirt VNC ports

---

**2. Resize Confirm / Revert**

Saat ini resize langsung dieksekusi (live hotplug vCPU + RAM).
OpenStack asli memiliki dua langkah: VERIFY_RESIZE → confirm atau revert.

```
Alur OpenStack Nova resize:
POST /servers/{id}/resize         -> status: VERIFY_RESIZE
POST /servers/{id}/confirmResize  -> size berubah permanen
POST /servers/{id}/revertResize   -> rollback ke flavor lama
```

**Pekerjaan:**
- Simpan `original_flavor_snapshot` saat resize dimulai
- Status server: `RESIZE` → `VERIFY_RESIZE`
- Endpoint baru: `POST /servers/{id}/confirmResize` dan `/revertResize`
- Worker: hotplug vCPU/RAM saat `confirm`, rollback saat `revert`

---

**3. Shelve / Unshelve**

Shelve adalah "hibernate ke object storage" — VM di-snapshot ke image,
kemudian instance dihentikan dan resource (vCPU, RAM) dibebaskan.
Berbeda dengan suspend yang menyimpan state ke disk tapi tetap pakai resource.

```
POST /servers/{id}/shelve
  -> buat snapshot image dari VM saat ini
  -> stop VM
  -> hapus libvirt domain (bebaskan resource)
  -> status: SHELVED_OFFLOADED

POST /servers/{id}/unshelve
  -> buat VM baru dari snapshot image
  -> hapus snapshot image sementara
  -> status: ACTIVE
```

**Pekerjaan:**
- Endpoint `POST /servers/{id}/shelve` dan `/unshelve`
- Worker perlu bisa membuat snapshot dan restore dari snapshot
- Perlu integrasi dengan image-api untuk menyimpan shelved image

---

**4. Lock / Unlock Server**

Fitur sederhana yang mencegah accidental modification.

```python
POST /servers/{id}/lock    # tandai server sebagai locked
POST /servers/{id}/unlock  # lepas lock

# Semua operasi modifikasi harus cek:
if server.locked and not is_admin:
    raise HTTPException(403, "Server is locked")
```

**Pekerjaan:**
- Tambah kolom `locked` (boolean) dan `locked_by` di tabel `servers`
- Endpoint `POST /servers/{id}/lock` dan `POST /servers/{id}/unlock`
- Guard semua action endpoint dengan cek lock status

---

**5. Server Tags**

Simple tagging untuk server.

```
GET    /servers/{id}/tags        — list tags
PUT    /servers/{id}/tags        — replace all tags
PUT    /servers/{id}/tags/{tag}  — add tag
DELETE /servers/{id}/tags/{tag}  — delete tag
```

**Pekerjaan:**
- Tambah tabel `server_tags` (server_id, tag) di DB
- Implementasi 4 endpoint di atas
- Tambah filter `?tags=` di `GET /servers`

---

#### 🟡 Prioritas Menengah

**6. Aggregates & Availability Zones**

Diperlukan jika ingin simulasi multi-node di masa depan.

```
POST /os-aggregates              — buat aggregate
GET  /os-aggregates              — list aggregates
POST /os-aggregates/{id}/action  — add/remove host
```

**Pekerjaan:**
- Tambah tabel `aggregates` dan `aggregate_hosts`
- Availability zone otomatis dari aggregate
- Default AZ: `nova`

---

#### 🟢 Prioritas Rendah

- **Ephemeral Disk** — Sengaja dikecualikan (volume-first architecture)
- **Live Migration** — Single-node, tidak relevan
- **Config drive vfat** — ISO9660 sudah cukup
- **Trigger Crash Dump** — Tidak relevan lab

### Estimasi Upaya Nova

| Pekerjaan | Kompleksitas | Upaya |
|---|:---:|---|
| noVNC proxy | Tinggi | 3–5 hari |
| Resize confirm/revert | Sedang | 2 hari |
| Shelve/unshelve | Sedang | 2–3 hari |
| Lock/unlock | Rendah | 0.5 hari |
| Server tags | Rendah | 1 hari |
| Aggregates/AZ | Sedang | 2–3 hari |

**Target setelah dikerjakan: ~92%**

---

## 4. Neutron — `networking-api` (Saat ini: ~65%)

### Kondisi Kode Saat Ini

File utama: `services/networking/app/main.py` (~71KB — terbesar di proyek)

Sudah ada: network, subnet, router, floating IP, security group, port, OVN integration penuh,
provider network via localnet, ARP proxy.

### Gap yang Perlu Dikerjakan

#### 🔴 Prioritas Tinggi

**1. QoS (Quality of Service)**

OpenStack Neutron mendukung bandwidth limiting per port via QoS policy.
OVN sudah mendukung mekanisme ini melalui tabel `QoS` di NB DB.

```
Endpoint yang perlu ditambah:
POST /qos/policies                              — buat QoS policy
GET  /qos/policies                              — list
PATCH /qos/policies/{id}                        — update
DELETE /qos/policies/{id}                       — delete
POST /qos/policies/{id}/bandwidth_limit_rules   — tambah rule

# Terapkan ke port:
PATCH /ports/{id}  {"qos_policy_id": "..."}
```

**Pekerjaan di OVN:**
```python
# OVN NB DB: set QoS rule
ovn_nbctl("qos-add", ls_name, "from-lport", "100",
          f"inport == {port_id}", "rate=10000", "burst=20000")
```

---

**2. IPv6 Support** ✅ (Selesai di Fase 3)

Saat ini hanya IPv4. OpenStack mendukung dual-stack dan IPv6-only subnets.

```python
# Subnet create dengan IPv6:
POST /subnets {
  "ip_version": 6,
  "cidr": "fd00::/64",
  "ipv6_ra_mode": "slaac",
  "ipv6_address_mode": "slaac"
}
```

**Pekerjaan:**
- Extend IPAM untuk mendukung IPv6 CIDR
- OVN DHCPv6 options: `SET_DHCPV6_STATEFUL_OPT` atau SLAAC via RA
- Validasi `ip_version` 4 vs 6 di semua endpoint subnet
- Dual-stack port allocation (satu port dengan IPv4 + IPv6)

---

**3. Shared Networks (Cross-Project)**

OpenStack mendukung network yang bisa di-share antar project.
Berguna untuk provider network yang diakses semua project.

```python
# Create shared network:
POST /networks {"shared": true}
# Policy: shared network visible ke semua project saat GET /networks
```

**Pekerjaan:**
- Tambah kolom `shared` di tabel `networks`
- Query `GET /networks` harus return network milik project ATAU `shared=true`
- Validasi akses saat create port di shared network

---

**4. Subnet Pools**

Pool of subnets yang bisa dialokasikan dari prefix besar.

```
POST /subnetpools      — buat pool (misal: 10.0.0.0/8)
GET  /subnetpools      — list pools

# Saat create subnet:
POST /subnets {"subnetpool_id": "...", "prefixlen": 24}
# -> auto-allocate 10.x.y.0/24 dari pool
```

**Pekerjaan:**
- Tabel baru: `subnet_pools` (prefix, min_prefixlen, max_prefixlen, default_prefixlen)
- IPAM allocator yang tarik dari pool secara otomatis
- Validasi overlap antar alokasi

---

**5. Port Forwarding (DNAT per-port)** ✅ (Selesai di Fase 3)

Berbeda dari floating IP (yang memetakan satu IP publik ke satu VM).
Port forwarding memetakan `public_ip:port` → `vm_ip:port`.

```
POST /floatingips/{id}/port_forwardings {
  "protocol": "tcp",
  "external_port": 8080,
  "internal_port": 80,
  "internal_ip_address": "192.168.1.10"
}
```

**Pekerjaan di OVN:**
```python
# DNAT rule untuk port forwarding
ovn_nbctl("lr-nat-add", router_name, "dnat",
          f"{fip}:{ext_port}", f"{internal_ip}:{int_port}",
          protocol="tcp")
```

---

#### 🟡 Prioritas Menengah

**6. Trunk Ports (VLAN-in-VLAN)**

Diperlukan untuk NFV dan nested VM scenarios.

```
POST /trunks {
  "port_id": "parent_port_id",
  "sub_ports": [
    {"port_id": "...", "segmentation_type": "vlan", "segmentation_id": 100}
  ]
}
```

OVN sudah mendukung trunk via `parent_port`, perlu wrap API.

---

**7. DNS Integration (Tanpa Designate)**

Implementasi DNS internal sederhana tanpa Designate penuh:

**Pekerjaan minimal:**
- Tambah kolom `dns_domain` di network
- Tambah kolom `dns_name` di port
- OVN mendukung DNS via tabel `DNS` di NB DB

---

#### 🟢 Prioritas Rendah

- **Address Scopes** — Advanced routing, di luar scope lab
- **BGP Dynamic Routing** — Out of scope
- **VPN as a Service** — Out of scope
- **FWaaS** — Security group sudah cukup
- **Load Balancer (Octavia)** — Kompleksitas sangat tinggi

### Estimasi Upaya Neutron

| Pekerjaan | Kompleksitas | Upaya |
|---|:---:|---|
| QoS policies | Sedang | 2–3 hari |
| IPv6 support | Tinggi | 4–6 hari |
| Shared networks | Rendah | 1 hari |
| Subnet pools | Sedang | 2–3 hari |
| Port forwarding | Sedang | 2 hari |
| Trunk ports | Tinggi | 3–4 hari |
| DNS integration | Rendah | 1–2 hari |

**Target setelah dikerjakan: ~82%**

---

## 5. Glance — `image-api` (Saat ini: ~70%)

### Kondisi Kode Saat Ini

File utama: `services/image/app/main.py` (~15KB)

Sudah ada: upload/download, list/show/delete, deactivate/reactivate,
visibility public/private, backend file + MinIO.

### Gap yang Perlu Dikerjakan

#### 🔴 Prioritas Tinggi

**1. Image Members (Sharing antar Project)**

Glance mendukung berbagi image private ke project lain secara eksplisit.

```
POST /images/{id}/members {"member": "target_project_id"}  -> status: "pending"
PUT  /images/{id}/members/{member_id} {"status": "accepted"}
GET  /images/{id}/members     — list members
DELETE /images/{id}/members/{id}
```

**Pekerjaan:**
- Tabel baru: `image_members` (image_id, member_id, status)
- Endpoint CRUD image members
- Query `GET /images` harus include image yang diterima oleh project saat ini
- Status workflow: pending → accepted/rejected

---

**2. Image Tags**

Metadata sederhana berupa list of strings.

```python
POST /images/{id}/tags/{tag}    — tambah tag
DELETE /images/{id}/tags/{tag}  — hapus tag
GET /images?tag=linux           — filter by tag
```

**Pekerjaan:**
- Tambah kolom `tags` (JSON array) di `images`
- Endpoint `POST/DELETE /images/{id}/tags/{tag}`
- Filter `?tag=` di endpoint list

---

**3. Task-based Import (Async dari URL)**

Saat ini upload synchronous. Glance asli punya task API untuk import async dari URL.

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

**Pekerjaan:**
- Tabel baru: `image_tasks`
- Background worker menggunakan pola RabbitMQ cast yang sudah ada
- Endpoint `POST /v2/tasks` dan `GET /v2/tasks/{id}`

---

**4. Pagination & Advanced Filtering**

Saat ini filter hanya dasar (name, visibility).

```
GET /images?sort=name:asc&limit=20&marker=<last_id>
GET /images?status=active&disk_format=qcow2&min_disk=10&max_disk=100
```

**Pekerjaan:**
- Tambah parameter `limit`, `marker`, `sort` di `GET /images`
- Tambah filter: `status`, `disk_format`, `container_format`, `min_disk`, `max_disk`, `min_ram`

---

**5. Image Caching (di image-api layer)**

Saat ini caching hanya di compute-worker.

**Pekerjaan:**
- Implementasi cache layer dengan TTL di image-api
- Cache metadata di Redis (opsional)

---

#### 🟡 Prioritas Menengah

**6. Image Signing & Verification**

Versi sederhana tanpa Barbican penuh:
- Tandatangani image hash dengan private key
- Simpan signature di image properties
- Verify saat compute-worker download image

---

#### 🟢 Prioritas Rendah

- **Multi-store backend (Ceph, Swift, HTTP)** — File + MinIO sudah cukup
- **Image encryption** — Memerlukan Barbican, out of scope

### Estimasi Upaya Glance

| Pekerjaan | Kompleksitas | Upaya |
|---|:---:|---|
| Image members | Sedang | 2 hari |
| Image tags | Rendah | 0.5 hari |
| Task-based import | Sedang | 2–3 hari |
| Pagination & filtering | Rendah | 1 hari |
| Image caching | Sedang | 1–2 hari |
| Image signing | Tinggi | 3 hari |

**Target setelah dikerjakan: ~88%**

---

## 6. Cinder — `block-store-api` + `block-store-worker` (Saat ini: ~75%)

### Kondisi Kode Saat Ini

File API: `services/block_store/app/main.py` (~36KB)
Worker: `services/block_store_worker/`
Drivers: `services/block_store/app/drivers/`

Sudah ada: volume CRUD, attach/detach, extend, snapshot, backup, volume types,
LVM + Ceph dual backend, boot-from-volume, transfer.

### Gap yang Perlu Dikerjakan

#### 🔴 Prioritas Tinggi

**1. Quota Management per Project**

Cinder mendukung quota (max volumes, max snapshots, max GB) per project.
Saat ini tidak ada batasan apapun.

```
GET  /os-quota-sets/{project_id}         — tampilkan quota
PUT  /os-quota-sets/{project_id}         — set quota (admin)
DELETE /os-quota-sets/{project_id}       — reset ke default
GET  /os-quota-sets/{project_id}/detail  — usage + limit

# Default quotas (Cinder default):
volumes: 10
snapshots: 10
gigabytes: 1000
```

**Pekerjaan:**
- Tabel baru: `volume_quotas` (project_id, resource, hard_limit)
- Tabel baru: `quota_usages` (project_id, resource, in_use, reserved)
- Middleware yang cek quota sebelum create volume/snapshot
- Endpoint CRUD quota sets

---

**2. LVM Thin Provisioning** ✅ (Selesai di Fase 3)

Saat ini hanya LVM thick (alokasi penuh langsung).
Thin provisioning hemat space — alokasi space hanya saat data ditulis.

```bash
# Saat ini (thick):
lvcreate -L 10G -n volume-id vg-cloudx

# Thin provisioning:
# 1. Buat thin pool (sekali):
lvcreate -L 100G --thinpool thinpool vg-cloudx
# 2. Buat thin LV dari pool:
lvcreate -V 10G --thin -n volume-id vg-cloudx/thinpool
# Keuntungan: 100G pool bisa berisi 200G+ thin volumes (over-provisioning)
```

**Pekerjaan:**
- Tambah opsi `lvm_type: thin|thick` di volume type
- Driver LVM: implementasi thin pool management
- Deteksi dan inisialisasi thin pool saat startup

---

**3. Volume Quota Enforcement dengan Reserved State**

Saat volume sedang dibuat (status `creating`), quota harus sudah di-reserved
agar tidak ada race condition.

```python
# Alur yang benar:
def create_volume(project_id, size):
    if usage + size > hard_limit:
        raise QuotaExceeded()
    reserve_quota(project_id, "gigabytes", size)  # atomic reserve
    rpc.cast("block-store-worker", "create_volume", {...})
    # Worker: commit atau rollback reservation saat selesai/error
```

---

**4. Multi-Attach Volume** ✅ (Selesai di Fase 3)

Cinder mendukung volume yang di-attach ke lebih dari satu server sekaligus
(khusus untuk volume type yang support multi-attach, biasanya dengan Ceph).

```python
POST /volumes {"multiattach": true, ...}

# Attach ke server kedua meskipun sudah attach ke server pertama
POST /volumes/{id}/attach {"server_id": "server2_id", "mountpoint": "/dev/vdb"}
```

**Pekerjaan:**
- Tambah kolom `multiattach` di tabel volumes
- Ubah constraint attach: jika `multiattach=true`, izinkan lebih dari satu attachment
- Ceph RBD sudah mendukung multi-reader

---

**5. QoS Enforcement (cgroups / blkio)**

Saat ini `max_iops` dan `max_throughput` di volume type hanya informational.
Cinder asli enforce via storage backend.

```bash
# Ceph RBD throttle:
rbd config image set pool/image-id rbd_qos_iops_limit 1000
rbd config image set pool/image-id rbd_qos_bps_limit 104857600

# LVM blkio throttle via cgroupv2:
# /sys/fs/cgroup/blkio/blkio.throttle.read_iops_device
```

**Pekerjaan:**
- Driver Ceph: set `rbd_qos_iops_limit` dan `rbd_qos_bps_limit` via `rbd config`
- Driver LVM: implementasi blkio rate limiting saat attach

---

#### 🟡 Prioritas Menengah

**6. Volume Migration (antar backend)**

Pindah volume dari LVM ke Ceph atau sebaliknya.

```
POST /volumes/{id}/action {"os-migrate_volume": {"host": "ceph-backend"}}
```

**Pekerjaan:**
- `qemu-img convert` antara LVM block device dan Ceph RBD
- Atomic swap: hapus volume lama setelah copy selesai

---

**7. Consistency Groups**

Grup volume yang snapshot-nya diambil secara atomik bersama.
Berguna untuk database backup.

```
POST /consistencygroups   — buat group
POST /cgsnapshots         — snapshot semua volume dalam group sekaligus
```

---

#### 🟢 Prioritas Rendah

- **Volume Encryption** — Memerlukan Barbican
- **Volume Replication** — Diperlukan HA, di luar scope single-node
- **iSCSI / NFS / FC backend** — Hanya LVM + Ceph sudah cukup untuk lab

### Estimasi Upaya Cinder

| Pekerjaan | Kompleksitas | Upaya |
|---|:---:|---|
| Quota management | Sedang | 2–3 hari |
| LVM thin provisioning | Tinggi | 3–4 hari |
| Quota enforcement (reserved) | Sedang | 1–2 hari |
| Multi-attach volume | Sedang | 2 hari |
| QoS enforcement | Tinggi | 3–5 hari |
| Volume migration | Sedang | 2–3 hari |

**Target setelah dikerjakan: ~88%**

---

## 7. Placement — `placement-api` (Saat ini: ~70%)

### Kondisi Kode Saat Ini

File utama: `services/placement/app/main.py` (~16KB)

Sudah ada: resource providers, inventory (VCPU/MEMORY_MB/DISK_GB),
traits, allocations, usages.

### Gap yang Perlu Dikerjakan

#### 🔴 Prioritas Tinggi

**1. Allocation Candidates**

Ini adalah fitur paling penting yang hilang — Nova scheduler menggunakannya
untuk menemukan host yang memenuhi syarat.

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

**Pekerjaan:**
- Query: cari provider yang punya capacity >= requested resources
- Hitung available = `total * allocation_ratio - reserved - used`
- Return filtered list dengan summaries
- Filter by traits (required/forbidden)

---

**2. Nested Resource Providers** ✅ (Selesai di Fase 3)

Hierarki provider: Node → NUMA node → CPU set.
Diperlukan untuk NUMA-aware scheduling dan SR-IOV.

```
Resource Provider Tree:
compute-host (root)
├── NUMA node 0
│   └── CPU 0-7
└── NUMA node 1
    └── CPU 8-15
```

**Pekerjaan:**
- Tambah kolom `parent_provider_uuid` di `resource_providers`
- API: `GET /resource_providers?in_tree={root_uuid}`
- `GET /resource_providers/{uuid}/aggregates`

---

**3. API Microversions**

OpenStack Placement menggunakan microversioning untuk evolusi API
tanpa breaking change.

```
# Request header:
OpenStack-API-Version: placement 1.36

# Response header:
OpenStack-API-Version: placement 1.36
x-openstack-request-id: req-...
```

**Pekerjaan:**
- Middleware yang parse `OpenStack-API-Version: placement X.Y`
- Mapping versi ke feature flags
- Header `x-openstack-request-id` di semua response

---

**4. Reshaper (Atomic Inventory + Allocation Update)**

Untuk update inventory dan allocation secara atomik.
Diperlukan saat resize atau live migration.

```
POST /reshaper {
  "inventories": {"provider-uuid": {"resource_class": {...}}},
  "allocations": {"consumer-uuid": {"allocations": {...}}}
}
```

**Pekerjaan:**
- Endpoint `POST /reshaper`
- Wrapped dalam satu database transaction

---

**5. Custom Resource Classes**

Selain VCPU/MEMORY_MB/DISK_GB, OpenStack mendukung custom resource class
(FPGA, GPU, VGPU, SRIOV_NET_VF, dll.)

```
PUT /resource_classes/CUSTOM_GPU_A100  — buat custom resource class
GET /resource_classes                  — list semua (standard + custom)
```

---

#### 🟡 Prioritas Menengah

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

#### 🟢 Prioritas Rendah

- **Full scheduler integration** — Relevan hanya di multi-node

### Estimasi Upaya Placement

| Pekerjaan | Kompleksitas | Upaya |
|---|:---:|---|
| Allocation candidates | Sedang | 2–3 hari |
| Nested resource providers | Tinggi | 3–4 hari |
| API microversions | Rendah | 1 hari |
| Reshaper | Sedang | 1–2 hari |
| Custom resource classes | Rendah | 1 hari |
| Aggregate association | Rendah | 1 hari |

**Target setelah dikerjakan: ~88%**

---

## 8. Horizon — `dashboard` (Saat ini: ~55%)

### Kondisi Kode Saat Ini

Frontend: `services/dashboard/frontend/src/`

Pages yang sudah ada: Admin, Flavors, FloatingIPs, Images, Keypairs, Login,
Networks, Overview, Routers, SecurityGroups, ServerGroups, Servers,
Snapshots, VolumeTypes, Volumes

Stack: React + Vite + dark theme premium

### Gap yang Perlu Dikerjakan

#### 🔴 Prioritas Tinggi

**1. noVNC Console (In-Browser)**

Fitur paling dicari dari web dashboard. Memerlukan noVNC proxy (dibahas di bagian Nova).

```jsx
// Di Servers.jsx, saat klik tombol "Console":
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

**Pekerjaan:**
- Integrasi noVNC JS library ke frontend
- Modal/page baru untuk console view
- Token-based security untuk WebSocket connection

---

**2. Live Log Streaming**

Saat ini log adalah snapshot (fetch sekali). Perlu auto-refresh.

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

**Pekerjaan:**
- Komponen `LogViewer` dengan auto-refresh setiap 2 detik
- Gunakan WebSocket existing untuk push update ketika status berubah
- Scroll-to-bottom otomatis

---

**3. Network Topology View**

Visualisasi graph: Server ↔ Port ↔ Network ↔ Router ↔ Floating IP.

```jsx
import ReactFlow from 'reactflow';

const TopologyPage = () => {
  const { nodes, edges } = buildTopologyGraph(networks, routers, servers);
  return <ReactFlow nodes={nodes} edges={edges} />;
};
```

**Pekerjaan:**
- Install library: `react-flow-renderer` atau `cytoscape.js`
- Halaman baru: `Topology.jsx`
- Build graph dari API data (networks, routers, servers, floating IPs)
- Klik node → show detail panel

---

**4. Image Upload via UI (Drag & Drop)**

Saat ini tidak ada upload image dari dashboard.

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

**Pekerjaan:**
- Gunakan `react-dropzone` untuk drag & drop
- Progress bar upload dengan `XMLHttpRequest` (mendukung `onprogress`)
- Preview metadata sebelum upload (format, size, checksum)

---

**5. Project Selector (Live Switch)**

Saat ini login hanya untuk satu project per session.
Horizon memiliki project switcher di header.

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

**Pekerjaan:**
- Endpoint `POST /auth/tokens` dengan `scope.project` baru
- Store token baru di context
- Re-fetch semua resource setelah switch

---

**6. Quota Usage Display**

Dashboard overview harus menampilkan usage vs quota.

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

**Pekerjaan:**
- Endpoint placement `GET /usages` sudah ada — expose ke dashboard
- Tambah quota endpoint di Cinder dan Keystone
- Widget baru di halaman Overview

---

#### 🟡 Prioritas Menengah

**7. Identity: Roles Management**

Halaman admin untuk manage role assignments per user per project.

```
Admin -> Users -> alice -> Roles -> [admin, member, reader] (checkboxes)
Admin -> Projects -> myproject -> Members -> [alice:admin, bob:member]
```

---

**8. API Access Panel**

Tampilkan credentials dan endpoint URLs untuk setiap service.

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

Saat ini banyak resource hanya tampil di list tanpa halaman detail.
Perlu: Server detail page, Volume detail page, Network detail page.

---

#### 🟢 Prioritas Rendah

- **Object Storage UI (Swift/MinIO)** — Out of scope base services
- **Heat / Orchestration UI** — Service belum diimplementasikan

### Estimasi Upaya Horizon

| Pekerjaan | Kompleksitas | Upaya |
|---|:---:|---|
| noVNC console | Tinggi | 3–5 hari |
| Live log streaming | Rendah | 1 hari |
| Network topology view | Tinggi | 3–5 hari |
| Image upload via UI | Sedang | 2 hari |
| Project selector | Sedang | 1–2 hari |
| Quota usage display | Rendah | 1 hari |
| Roles management | Sedang | 2 hari |
| API access panel | Rendah | 1 hari |

**Target setelah dikerjakan: ~82%**

---

## 9. Infrastruktur Cross-Cutting

Beberapa pekerjaan lintas service yang perlu dikerjakan bersamaan.

### 9.1 API Versioning ✅ (Selesai di Fase 3)

OpenStack semua service menggunakan versioning: `/v2/`, `/v3/`, microversions.
Saat ini cloudx tidak punya prefix versi.

```
Saat ini:    GET /servers
OpenStack:   GET /v2.1/servers  (Nova)
             GET /v3/tokens     (Keystone)
             GET /v2/images     (Glance)
```

**Pekerjaan:**
- Tambah prefix versi di setiap service router
- Header `X-OpenStack-Request-Id` di setiap response
- Endpoint `GET /` (version discovery)

---

### 9.2 Request ID Tracing

OpenStack meng-generate `x-openstack-request-id` di setiap request.

```python
@app.middleware("http")
async def add_request_id(request: Request, call_next):
    req_id = f"req-{uuid.uuid4()}"
    response = await call_next(request)
    response.headers["x-openstack-request-id"] = req_id
    return response
```

**Pekerjaan:**
- Tambah middleware ke semua service
- Propagate request ID dalam RPC calls antar service

---

### 9.3 Quota System Terpusat

Daripada tiap service punya quota sendiri, lebih baik punya satu sistem quota
yang konsisten (mirip `oslo.quota`).

```python
# services/shared/quota.py:
class QuotaEngine:
    def reserve(self, project_id, resource, delta): ...
    def commit(self, reservations): ...
    def rollback(self, reservations): ...
```

---

### 9.4 Pagination Standard

Semua service harus pakai pagination format yang konsisten.

```
GET /servers?limit=20&marker=<last_server_id>
# response + "next" link jika ada halaman berikutnya
```

---

### 9.5 Event Notification Standard

Saat ini hanya compute dan block-store yang punya event notifications.
Semua service harus bisa publish events standar.

```python
# Event types standar OpenStack:
"compute.instance.create.start"
"compute.instance.create.end"
"volume.create.start"
"volume.create.end"
"network.create.end"
```

---

### 9.6 Health Check Standard

Semua service harus implementasi health check endpoint yang seragam.

```
GET /healthcheck -> {"status": "up", "checks": {"db": "ok", "rabbitmq": "ok"}}
```

---

## 10. Prioritas dan Estimasi Upaya

### Matriks Dampak vs Upaya

```
Dampak Tinggi ──────────────────────────────────────────────
                   | Quick Wins (Upaya Rendah) | Big Bets (Upaya Tinggi) |
                   |---------------------------|-------------------------|
                   | Server tags               | noVNC console           |
                   | Lock/unlock server        | IPv6 support            |
                   | Image tags                | LVM thin provisioning   |
                   | Shared networks           | QoS enforcement         |
                   | Service catalog           | Network topology UI     |
───────────────────|---------------------------|-------------------------|
Dampak Rendah      | API microversions         | Volume migration        |
                   | Request ID tracing        | Volume replication      |
                   | Health check standard     | Consistency groups      |
                   | Pagination standard       | BGP routing             |
                   Upaya Rendah                     Upaya Tinggi
```

---

### ✅ Fase 1 — Quick Wins *(Selesai)*

| # | Pekerjaan | Service | Status |
|---|---|---|:---:|
| 1 | Server tags | Nova | ✅ |
| 2 | Lock/unlock server | Nova | ✅ |
| 3 | Image tags | Glance | ✅ |
| 4 | Image members (sharing) | Glance | ✅ |
| 5 | Shared networks | Neutron | ✅ |
| 6 | Quota management basic | Cinder | ✅ |
| 7 | Pagination standard semua service | Cross-cutting | ✅ |
| 8 | Request ID tracing | Cross-cutting | ✅ |
| 9 | Service catalog | Keystone | ✅ |
| 10 | Quota display di dashboard | Horizon | ✅ |
| 11 | Live log streaming di dashboard | Horizon | ✅ |
| 12 | Image upload via UI | Horizon | ✅ |
| 13 | Project selector | Horizon | ✅ |

**Skor setelah Fase 1:**
Keystone ~75% · Nova ~84% · Neutron ~68% · Glance ~80% · Cinder ~80% · Horizon ~68%

---

### ✅ Fase 2 — Core Features *(Selesai)*

| # | Pekerjaan | Service | Status |
|---|---|---|:---:|
| 1 | noVNC proxy + console UI | Nova + Horizon | ✅ |
| 2 | Allocation candidates | Placement | ✅ |
| 3 | Resize confirm/revert | Nova | ✅ |
| 4 | Shelve/unshelve | Nova | ✅ |
| 5 | QoS policies (OVN-based) | Neutron | ✅ |
| 6 | Domain management | Keystone | ✅ |
| 7 | Task-based image import | Glance | ✅ |
| 8 | Subnet pools | Neutron | ✅ |
| 9 | Network topology view | Horizon | ✅ |
| 10 | API microversions | Placement | ✅ |
| 11 | Reshaper endpoint | Placement | ✅ |

**Skor setelah Fase 2:**
Keystone ~82% · Nova ~90% · Neutron ~78% · Glance ~86% · Cinder ~82% · Placement ~85% · Horizon ~78%

---

### ✅ Fase 3 — Polish *(Selesai)*

| # | Pekerjaan | Service | Status |
|---|---|---|:---:|
| 1 | IPv6 support (SLAAC, DHCPv6 stateful/stateless) | Neutron | ✅ |
| 2 | LVM thin provisioning (`lvm_type=thin`, auto thin pool) | Cinder | ✅ |
| 3 | Multi-attach volume (`multiattach=true`) | Cinder | ✅ |
| 4 | Central oslo.policy RBAC (`shared/policy.py`) | Keystone | ✅ |
| 5 | Port forwarding DNAT per-port | Neutron | ✅ |
| 6 | Nested resource providers + Aggregates | Placement | ✅ |
| 7 | API version discovery (`GET /`, `GET /v2.1`, dll.) | Cross-cutting | ✅ |

**Skor setelah Fase 3:**
Keystone ~87% · Nova ~90% · Neutron ~82% · Glance ~84% · Cinder ~88% · Placement ~88% · Horizon ~70%

---

### ✅ Fase 4 — Advanced Integration (Selesai)

Fase ini berfokus pada fitur-fitur lanjutan yang meningkatkan paritas secara signifikan,
terutama di sisi **Horizon**, **integrasi antar-service**, dan **hardening** sistem yang sudah ada.

#### 🔴 Prioritas Tinggi

| # | Pekerjaan | Service | Status | Catatan |
|---|---|---|:---:|---|
| 1 | **noVNC Console (In-Browser)** | Nova + Horizon | ✅ | Websockify container, token auth WebSocket |
| 2 | **Network Topology View** | Horizon | ✅ | React Flow / Cytoscape.js, klik node → detail |
| 3 | **Application Credentials** | Keystone | ✅ | `/v3/users/{id}/application_credentials`, scoped roles |
| 4 | **Multi-Scope Token** (domain/system scope) | Keystone | ✅ | Extend JWT dengan `scope_type` field |
| 5 | **Ceph QoS Enforcement** | Cinder Worker | ✅ | `rbd config image set rbd_qos_iops_limit` |
| 6 | **Quota Reserved State** (atomic reserve) | Cinder | ✅ | Reserve sebelum create, rollback jika error |
| 7 | **Custom Resource Classes** | Placement | ✅ | `PUT /resource_classes/CUSTOM_GPU_A100` |
| 8 | **Forbidden Traits Filter** di allocation_candidates | Placement | ✅ | `?required=X&forbidden=Y` query params |
| 9 | **Identity: Roles Management UI** | Horizon | ✅ | Halaman admin manage role assignments |
| 10 | **API Access Panel** | Horizon | ✅ | Tampilkan endpoint catalog + access keys |

#### 🟡 Prioritas Menengah

| # | Pekerjaan | Service | Status | Catatan |
|---|---|---|:---:|---|
| 11 | **Trunk Ports** (VLAN-in-VLAN) | Neutron | ✅ | OVN parent_port, wrapping API |
| 12 | **DNS Integration** (OVN internal DNS) | Neutron | ✅ | Kolom `dns_name` port, tabel `DNS` di OVN NB |
| 13 | **Dual-Stack Port** (IPv4 + IPv6 sekaligus) | Neutron | ✅ | `fixed_ips` array dengan dua entry |
| 14 | **Image Signing & Verification** | Glance + Nova | ✅ | Hash signature di properties, verify saat download |
| 15 | **Image Advanced Filtering** | Glance | ✅ | `?min_disk=`, `?disk_format=`, `?sort=` |
| 16 | **Volume Migration** (LVM ↔ Ceph) | Cinder | ✅ | `qemu-img convert`, atomic swap setelah copy |
| 17 | **Aggregates & AZ** (Nova) | Nova | ✅ | `/os-aggregates`, AZ otomatis dari aggregate |
| 18 | **Resource Detail Pages** | Horizon | ✅ | Server detail, Volume detail, Network detail pages |
| 19 | **Enforce policy.py di setiap endpoint** | Cross-cutting | ✅ | Integrasi `policy.enforce()` per action |
| 20 | **Centralized Quota System** (`shared/quota.py`) | Cross-cutting | ✅ | oslo.quota-inspired, atomic reserve/commit/rollback |

#### 🟢 Prioritas Rendah

| # | Pekerjaan | Service | Status | Catatan |
|---|---|---|:---:|---|
| 21 | Consistency Groups | Cinder | ✅ | Atomic snapshot grup volume |
| 22 | Image Caching (TTL) | Glance | ✅ | Cache metadata, opsional Redis |
| 23 | Health Check Standard | Cross-cutting | ✅ | `GET /healthcheck` → `{db: ok, rabbitmq: ok}` |
| 24 | Event Notification Standard | Cross-cutting | ✅ | `compute.instance.create.start/end` di semua service |

**Target skor setelah Fase 4:**
Keystone ~93% · Nova ~94% · Neutron ~88% · Glance ~90% · Cinder ~93% · Placement ~92% · Horizon ~85%

---

### Total Estimasi Keseluruhan

| Fase | Durasi | Jumlah Item | Status |
|---|:---:|:---:|:---:|
| ✅ Fase 1 — Quick Wins | 2–3 minggu | 13 item | **Selesai** |
| ✅ Fase 2 — Core Features | 3–5 minggu | 11 item | **Selesai** |
| ✅ Fase 3 — Polish | 2–4 minggu | 7 item | **Selesai** |
| ✅ Fase 4 — Advanced Integration | 4–6 minggu | 24 item | **Selesai** |
| 🚀 Fase 5 — Production Readiness | 4–6 minggu | 10 item | **Berikutnya** |
| **Total (F1–F5)** | **15–24 minggu** | **65 item** | — |

---

### 🚀 Fase 5 — Production Readiness & Day-2 Operations (Estimasi: 4–6 minggu)

Setelah semua layanan inti (base services) mencapai tingkat paritas yang tinggi dengan OpenStack, fokus beralih ke stabilitas operasional, skalabilitas, dan hardening agar CloudX siap untuk environment pseudo-production atau lab yang lebih serius.

#### 🔴 Prioritas Tinggi
| # | Pekerjaan | Area | Upaya | Catatan |
|---|---|---|:---:|---|
| 1 | **High Availability (HA) API Services** | Infrastruktur | 3-5 hari | ✅ HAProxy multi-backend round-robin, RabbitMQ 3-node cluster (`docker-compose.ha.yml`). |
| 2 | **Database Replication & Backup** | Infrastruktur | 4-5 hari | ✅ PostgreSQL primary+standby streaming replication, cron backup 23:00 Asia/Jakarta ke MinIO. |
| 3 | **Centralized Logging & Telemetry** | Cross-cutting | 5-7 hari | ✅ Prometheus (retention 15d) + Grafana + exporters + `/metrics` per service. |

#### 🟡 Prioritas Menengah
| # | Pekerjaan | Area | Upaya | Catatan |
|---|---|---|:---:|---|
| 4 | **Zero-Downtime Migrations** | Infrastruktur | 3-4 hari | ✅ Alembic scaffold (`services/migrations/`), baseline + additive-first rules. |
| 5 | **CI/CD & Automated Testing** | Infrastruktur | 5 hari | ⏭️ Ditunda (belum direncanakan user). |
| 6 | **Security Hardening (TLS/SSL)** | Cross-cutting | 3-4 hari | ✅ Internal PKI (`services/certs/generate-certs.sh`), TLS antar-service internal saja. |
| 7 | **Volume Backups to Object Storage** | Cinder | 3-4 hari | ✅ `POST /volumes/{id}/backup` + worker export → MinIO, restore ke volume baru. |

#### 🟢 Prioritas Rendah (Ekspansi Ekosistem)
| # | Pekerjaan | Area | Upaya | Catatan |
|---|---|---|:---:|---|
| 8 | **Heat (Orchestration)** | Service Baru | 7-10 hari | ⏭️ Ditunda ke fase berikutnya. |
| 9 | **Octavia (Load Balancer)** | Service Baru | 10-14 hari | ⏭️ Ditunda ke fase berikutnya. |
| 10| **Magnum (Container Service)** | Service Baru | 10-14 hari | ⏭️ Ditunda ke fase berikutnya. |

**Catatan:** Multi-node deployment adaptif (Control Plane / Compute / Storage / Gateway)
diimplementasikan via compose overlays terpisah, tetap kompatibel dengan mode single-node.
Detail operasional: `docs/fase5-production-readiness.md`.

**Target akhir Fase 5:** CloudX bukan lagi sekadar lab API, melainkan sistem Cloud mandiri yang resilient, secure, dan terobservasi.

---

## 11. Arsitektur Target

### Tambahan Komponen yang Dibutuhkan

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
         │          ┌────▼────────────┐    <-- BARU
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

  PostgreSQL — tabel yang sudah ada (Fase 1–3) ✅:
    services, endpoints (service catalog)
    domains (domain management)
    server_tags, image_members, image_tasks
    volume_quotas, networking_port_forwardings
    subnet_pools, qos_policies, qos_rules
    image_tags, placement_rp_aggregates

  PostgreSQL — tabel baru (Fase 4):
    application_credentials (Keystone)
    os_aggregates, aggregate_hosts (Nova)
    networking_trunks, networking_trunk_subports (Neutron)
    resource_classes (Placement)
    quota_reservations (Cross-cutting)
```

### Tabel Database — Status

#### ✅ Sudah Diimplementasikan (Fase 1–3)

| Tabel | Service | Tujuan |
|---|---|---|
| `auth_services` | Keystone | Service catalog entries |
| `auth_endpoints` | Keystone | Endpoint per service per region |
| `auth_domains` | Keystone | Domain hierarchy |
| `compute_server_tags` | Nova | Server tagging |
| `image_members` | Glance | Image sharing antar project |
| `image_tasks` | Glance | Async import tasks |
| `block_store_volume_quotas` | Cinder | Hard limit per project |
| `networking_subnet_pools` | Neutron | Subnet address pool |
| `networking_qos_policies` | Neutron | QoS policy definitions |
| `networking_qos_rules` | Neutron | Bandwidth limit rules |
| `networking_port_forwardings` | Neutron | DNAT per-port forwarding |
| `placement_rp_aggregates` | Placement | RP → aggregate association |
| `auth_application_credentials` | Keystone | Application credentials |
| `compute_aggregates` | Nova | Host aggregate definitions |
| `compute_aggregate_hosts` | Nova | Host → aggregate membership |
| `networking_trunks` | Neutron | Trunk port parent |
| `networking_trunk_subports` | Neutron | VLAN sub-port entries |
| `placement_resource_classes` | Placement | Custom resource classes |
| `quota_reservations` | Cross-cutting | Atomic quota reserve tracking |
| `block_store_consistency_groups` | Cinder | Consistency Groups metadata |
| `block_store_cgsnapshots` | Cinder | Consistency Group Snapshots |

#### 🚀 Diperlukan di Fase 5 (Opsional / Ekstensi)

| Tabel | Service | Tujuan |
|---|---|---|
| `cinder_backups` | Cinder | Metadata backup volume ke MinIO |
| `heat_stacks` | Heat | Menyimpan status stack orkestrasi |
| `octavia_loadbalancers` | Octavia | Metadata load balancer |

---

## 📌 Catatan Penting

> **Catatan 1 — Scope Lab**
> Beberapa fitur **sengaja tidak dikerjakan** karena di luar scope lab single-node:
> Live migration, Evacuation, Multi-node scheduling, FWaaS, VPNaaS, LBaaS (Octavia),
> Barbican, Designate, Swift, Heat.

> **Catatan 2 — Backend Sudah Nyata**
> CloudX sudah menggunakan backend nyata (libvirt, OVN, LVM, Ceph).
> Mayoritas pekerjaan tersisa adalah di **API layer** dan **UI**, bukan di backend.

> **Catatan 3 — Kompatibilitas OpenStack Client**
> Untuk bisa dipakai dengan `openstack` CLI atau OpenStack SDK standar,
> prioritas utama adalah:
> 1. Service catalog di Keystone (client auto-discover endpoints)
> 2. API prefix versioning (`/v2/`, `/v3/`)
> 3. Response format yang sesuai Keystone v3 token format

---

## 📊 Progress Tracker

| Fase | Item Total | Selesai | Progress |
|---|:---:|:---:|:---:|
| Fase 1 — Quick Wins | 13 | 13 | ████████████ 100% |
| Fase 2 — Core Features | 11 | 11 | ████████████ 100% |
| Fase 3 — Polish | 7 | 7 | ████████████ 100% |
| Fase 4 — Advanced Integration | 24 | 24 | ████████████ 100% |
| Fase 5 — Production Readiness | 10 | 7 | ████████░░░░ 70% |
| **Keseluruhan** | **65** | **62** | **███████████░ 95%** |

---

*Dokumen ini dibuat berdasarkan analisis kode di `services/`, `docs/feature-comparison.md`, dan `README.md`.*
*Terakhir diperbarui: Akhir Agustus 2026 — Fase 4 selesai, Fase 5 (Production Readiness) direncanakan.*
