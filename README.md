# mini OpenStack lab

mini OpenStack lab adalah single-node real VM lab berbasis Docker Compose. API control plane tetap sederhana dan OpenStack-like, tetapi compute sekarang hanya mendukung libvirt + QEMU/KVM dengan tap VM yang dibind ke OVS `br-int` untuk OVN.

Project ini untuk lab lokal, bukan production. Tidak ada scheduler multi-node, placement service, live migration, Keystone, atau hardening production.

Fitur yang tersedia:

- Real VM single-node via libvirt/QEMU/KVM
- OVN DHCP/IPAM untuk port VM
- Metadata API Nova-like dengan debug URL host
- Provider network via `br-ex`/OVN localnet dan floating IP OVN NAT
- Security group sederhana berbasis OVN ACL
- Block storage (volume qcow2 attach/detach)
- Image service dengan backend file atau MinIO

---

## 🚀 Bootstrap (Fresh Node)

Jalankan **empat langkah** berikut secara berurutan saat pertama kali setup node baru.

### Langkah 0 — Konfigurasi `.env`

```bash
cp .env.example .env
nano .env   # sesuaikan variabel di bawah ini
```

Variabel penting yang perlu disesuaikan:

| Variabel | Contoh | Keterangan |
|---|---|---|
| `PROVIDER_INTERFACE` | `enp0s8` | Interface host-only adapter di VirtualBox |
| `PROVIDER_HOST_IP` | `192.168.10.102/24` | IP host di provider network (dipasang ke br-ex) |
| `PROVIDER_CIDR` | `192.168.10.0/24` | Subnet provider network |
| `PROVIDER_GATEWAY` | `192.168.10.1` | OVN router external IP (gateway di public-net) |
| `PROVIDER_ALLOCATION_START` | `192.168.10.110` | Rentang floating IP mulai dari |
| `PROVIDER_ALLOCATION_END` | `192.168.10.120` | Rentang floating IP sampai |
| `PRIVATE_CIDR` | `192.168.12.0/24` | Subnet private/tenant network |
| `PRIVATE_GATEWAY` | `192.168.12.1` | Gateway private network |

### Langkah 1 — Siapkan Node

```bash
sudo make node-bootstrap
newgrp libvirt   # aktifkan group tanpa re-login
```

Script `scripts/node-bootstrap.sh` melakukan:
1. Install packages: `qemu-kvm`, `libvirt`, `openvswitch-switch`, `ovn-host`, dll
2. Enable service `openvswitch-switch` dan `libvirtd`
3. Tambah user ke grup `libvirt` dan `kvm`
4. Buat direktori storage di `/var/lib/mini-openstack/`
5. Buat OVS bridge `br-int` (fail_mode=secure) dan `br-ex`, attach `PROVIDER_INTERFACE`
6. Konfigurasi host OVS untuk OVN (`ovn-remote`, `encap-type`, `bridge-mappings`)
7. Install `systemd` service `mini-openstack-network` — IP `PROVIDER_HOST_IP` otomatis dipasang ke `br-ex` setiap boot

> **Idempotent** — aman dijalankan ulang jika ada yang gagal.

### Langkah 2 — Jalankan Services

**Pilih salah satu** sesuai `IMAGE_STORAGE_BACKEND` di `.env`:

```bash
# Opsi A — Backend file/lokal (default)
make up

# Opsi B — Backend MinIO (jika IMAGE_STORAGE_BACKEND=minio di .env)
make up-minio
```

Install CLI tools:
```bash
make install-cli
```

Setelah `make install-cli`, tambahkan PATH:
```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

Verifikasi semua service berjalan:
```bash
make ps
compute host check
```

### Langkah 3 — Setup Lab Resources

**Opsi A — Otomatis (direkomendasikan):**

```bash
make lab-setup
```

Script akan membuat semua resource secara otomatis dan mendownload CirrOS.

---

**Opsi B — Manual (untuk memahami flow-nya):**

```bash
# 1. Buat provider network (public/external)
networking network create public-net \
    --external \
    --provider-network-type flat \
    --provider-physical-network physnet1

networking subnet create public-subnet \
    --network public-net \
    --cidr 192.168.10.0/24 \
    --gateway 192.168.10.254 \
    --no-dhcp \
    --dns 8.8.8.8 \
    --allocation-start 192.168.10.110 \
    --allocation-end 192.168.10.120

# 2. Buat private network (tenant)
networking network create private-net

networking subnet create private-subnet \
    --network private-net \
    --cidr 192.168.12.0/24 \
    --gateway 192.168.12.1 \
    --dhcp \
    --dns 8.8.8.8

# 3. Buat router dan hubungkan ke kedua network
networking router create router1
networking router add-interface router1 --subnet private-subnet
networking router set-gateway router1 public-net

# 4. Download CirrOS dan upload ke image service
wget -q https://download.cirros-cloud.net/0.6.2/cirros-0.6.2-x86_64-disk.img \
     -O /tmp/cirros-0.6.2.img
image upload cirros --file /tmp/cirros-0.6.2.img --disk-format qcow2
```

Setelah Opsi A atau B selesai, semua resource siap digunakan.

### ✅ Node Siap

```bash
# Buat keypair
compute keypair create mykey > mykey.pem && chmod 600 mykey.pem

# Buat VM
compute server create vm1 --image cirros --flavor tiny \
    --network private-net --key-name mykey

# Lihat status VM
compute server list
compute server console vm1
```

---

## 🔄 Setelah Reboot Node

Tidak perlu menjalankan `node-bootstrap` lagi. Cukup:

```bash
make start   # = make up + make lab-setup (keduanya idempotent)
```

Yang otomatis terjadi saat boot:
- `openvswitch-switch` start → `br-int` dan `br-ex` aktif kembali (OVS DB persists)
- `mini-openstack-network.service` → IP `PROVIDER_HOST_IP` dipasang ke `br-ex`
- `docker compose up` → OVN controller start dan konfigurasi host OVS secara otomatis

---

## 🔌 Service Ports

| Service | URL |
|---|---|
| networking-api (Neutron-like) | http://localhost:8001 |
| image-api (Glance-like) | http://localhost:8002 |
| compute-api (Nova-like) | http://localhost:8003 |
| compute-worker | http://localhost:8010 |
| block-store-api (Cinder-like) | http://localhost:8004 |
| metadata-api | http://localhost:8775 |
| MinIO API | http://localhost:9000 |
| MinIO Console | http://localhost:9001 |

Swagger API tersedia di `/docs` pada port masing-masing service.

---

## 🗂️ Makefile Reference

```
make node-bootstrap      # Langkah 1: siapkan host (sudo, sekali saja)
make up                  # Langkah 2A: jalankan services (backend: file)
make up-minio            # Langkah 2B: jalankan services + MinIO (backend: minio)
make lab-setup           # Langkah 3: buat lab resources (idempotent)
make start               # Shortcut: make up + make lab-setup
make start-minio         # Shortcut: make up-minio + make lab-setup
make install-cli         # Install CLI tools (compute, networking, image, dll)

make down                # Stop semua containers
make logs                # Tail logs semua services
make ps                  # Status containers

make cleanup-compute     # Hapus VM dan tap interface lab
make cleanup-network     # Hapus networking resources lab
make cleanup-all         # cleanup-compute + cleanup-network
make test-real-vm        # Integration test: buat VM nyata
make test-floating-ip    # Integration test: floating IP end-to-end
```

---

## 🏗️ Architecture

| Service | Peran |
|---|---|
| `networking-api` | Neutron-like API: IPAM, OVN logical switch/router/port, floating IP NAT, security group ACL |
| `image-api` | Glance-like API: upload/download image OS, backend file atau MinIO |
| `compute-api` | Nova-like API: server lifecycle, flavor, keypair |
| `compute-worker` | Provisioning VM nyata via libvirt, qcow2, NoCloud config-drive, tap, OVS, OVN |
| `block-store-api` | Cinder-like API: volume qcow2/raw, attach/detach ke domain libvirt |
| `metadata-api` | Nova metadata HTTP API (169.254.169.254-like) |
| `ovs-ovn-controller` | OVN controller yang manage host OVS (mount `/var/run/openvswitch` dari host) |
| `ovn-nb-db` / `ovn-sb-db` | OVN Northbound & Southbound database |
| `postgres` | Database tunggal `mini_openstack` |

`compute-worker` berjalan `privileged` dengan `network_mode: host` sehingga tap VM dibuat di namespace host yang sama dengan libvirt/QEMU. Container mendapat akses ke libvirt socket, `/dev/kvm`, `/dev/net/tun`, dan OVS socket.

---

## 🌐 Floating IP Workflow

```bash
# Lihat floating IP yang tersedia
networking network show public-net

# Alokasi floating IP
networking floating-ip create public-net

# Associate ke VM
networking floating-ip associate 192.168.10.110 --server vm1 --router router1

# Test dari host
ping 192.168.10.110
```

Floating IP adalah NAT mapping di OVN, bukan IP yang dipasang di VM:

```
Komputer/host-only network → 192.168.10.110 → OVN dnat_and_snat → 192.168.12.x (VM)
```

> **Catatan VirtualBox:** Agar komputer fisik (yang menjalankan VirtualBox) bisa ping floating IP, aktifkan **Promiscuous Mode → Allow All** pada adapter host-only di VM settings.

---

## 💻 Penggunaan CLI

### Compute

```bash
compute flavor list
compute flavor create custom --vcpus 2 --ram 2048 --disk 20

compute keypair create mykey > mykey.pem && chmod 600 mykey.pem
compute server create vm1 --image cirros --flavor tiny --network private-net --key-name mykey
compute server list
compute server show vm1
compute server console vm1          # Serial console (Ctrl+] untuk keluar)
compute server ssh-info vm1
compute server start vm1
compute server stop vm1
compute server delete vm1
```

### Networking

```bash
networking network list
networking subnet list
networking router list
networking floating-ip list
networking floating-ip create public-net
networking floating-ip associate <ip> --server vm1 --router router1
networking floating-ip disassociate <ip>
networking floating-ip delete <ip>
```

### Image

```bash
image list
image upload myimage --file ./image.qcow2 --disk-format qcow2
image show cirros
image delete myimage
```

### Block Store

```bash
block-store volume create vol1 --size 10
block-store volume attach vol1 --server vm1
block-store volume detach vol1
block-store volume list
block-store volume delete vol1
```

---

## 🔍 Debug & Diagnostik

```bash
# OVS & OVN
sudo ovs-vsctl show
sudo ovs-ofctl dump-flows br-int | wc -l
docker compose exec networking-api ovn-nbctl --db=tcp:ovn-nb-db:6641 show
docker compose exec networking-api ovn-nbctl --db=tcp:ovn-nb-db:6641 lr-nat-list router1
docker compose exec ovn-sb-db ovn-sbctl --db=tcp:ovn-sb-db:6642 show

# Libvirt
virsh -c qemu:///system list --all

# Metadata
server_id="$(compute server show vm1 | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
curl "http://localhost:8775/openstack/latest/meta_data.json?instance_id=${server_id}"
```

---

## 🧹 Cleanup

```bash
# Dry-run (hanya tampilkan apa yang akan dihapus)
scripts/cleanup-compute.sh --dry-run
scripts/cleanup-network.sh --dry-run

# Eksekusi
sudo scripts/cleanup-all.sh --force
sudo scripts/cleanup-all.sh --force --with-volumes   # + hapus Docker volumes

# Reset lab tanpa reinstall node
make cleanup-all
make lab-setup
```

Cleanup hanya menarget resource dengan marker project (`mini-os-`, `test-mini-`, `tap-mini-`). Bridge `br-int`/`br-ex` tidak dihapus kecuali dengan flag eksplisit `--remove-bridges`.

---

## 📚 Docs

- [docs/real-vm.md](docs/real-vm.md)
- [docs/flavor.md](docs/flavor.md)
- [docs/ipam.md](docs/ipam.md)
- [docs/ovn-dhcp.md](docs/ovn-dhcp.md)
- [docs/provider-network.md](docs/provider-network.md)
- [docs/ovn-localnet.md](docs/ovn-localnet.md)
- [docs/br-ex.md](docs/br-ex.md)
- [docs/metadata-service.md](docs/metadata-service.md)
- [docs/floating-ip.md](docs/floating-ip.md)
- [docs/security-group-ovn-acl.md](docs/security-group-ovn-acl.md)
- [docs/console.md](docs/console.md)
- [docs/libvirt.md](docs/libvirt.md)
- [docs/cloud-init.md](docs/cloud-init.md)
- [docs/cleanup.md](docs/cleanup.md)
- [docs/troubleshooting.md](docs/troubleshooting.md)
