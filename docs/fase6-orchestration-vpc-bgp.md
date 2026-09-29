# 🚀 Phase 6 — Multi-Node Compute, Auto-Scaling & VPC Peering/BGP

Dokumen ini menjelaskan arsitektur, API endpoints, serta operasional dari fitur Fase 6: **Multi-Node Scheduler (Placement Extension)**, **Auto-Scaling Group Orchestration (Heat/Senlin-like)**, serta **VPC Peering & BGP Dynamic Routing (Neutron Extension)**.

---

## 📋 Service Overview

| Service | Port | Database | Deskripsi |
|---------|------|----------|-----------|
| `placement-api` | 8006 | `mini_openstack` | Dynamic node Heartbeat, multi-node compute node discovery, scheduling candidate resolution (`select_destination`). |
| `orchestration-api` | 8013 | `cloudx_orchestration` | Engine pengelola Auto-Scaling Group (ASG), evaluasi metrik otomatis, scaling-out & scaling-in. |
| `orchestration-worker` | N/A | `cloudx_orchestration` | Worker background yang mengevaluasi threshold metrik telemetry terhadap policy ASG setiap 30 detik. |
| `networking-api` | 8001 | `mini_openstack` | Inter-router VPC Peering via OVN logical patch ports & manajemen tetangga BGP dynamic routing. |

---

## 1. Multi-Node Scheduler (`placement-api` - Port 8006)

Memungkinkan cluster CloudX mengelola banyak compute node (hypervisor), memantau kesehatan node via heartbeat, dan memilih node tujuan komputasi optimal saat pembuatan VM atau live migration.

### API Endpoints Utama
- `POST /resource_providers/{rp_uuid}/heartbeat` — Compute node heartbeat pings.
- `GET /compute_nodes` — Daftar semua hypervisor / compute node terhubung beserta kapasitas CPU, Memory, Disk.
- `GET /allocation_candidates` — Pencarian compute node yang memenuhi kriteria flavor VM.
- `POST /scheduler/select_destination` — Algoritma seleksi compute node terbaik berdasarkan load/resource ketersediaan.

### CLI Usage
```bash
cloudx placement summary
cloudx placement node-list
```

---

## 2. Auto-Scaling Group Orchestration (`orchestration-api` - Port 8013)

Orchestrator kluster VM yang secara otomatis menambah (*scale-out*) atau mengurangi (*scale-in*) instance VM berdasarkan rata-rata beban telemetry (misal: target CPU > 70%).

### API Endpoints Utama
- `GET /scaling-groups` & `POST /scaling-groups` — Buat dan daftar Auto-Scaling Group.
- `GET /scaling-groups/{id}` & `PATCH /scaling-groups/{id}` — Detail & update kapasitas (min_instances, max_instances, target_cpu_percent, cooldown_seconds).
- `POST /scaling-groups/{id}/scale-out` — Trigger manual scale-out (tambah 1 instance VM).
- `POST /scaling-groups/{id}/scale-in` — Trigger manual scale-in (terminasi 1 instance VM).
- `GET /scaling-groups/{id}/instances` — Daftar VM aktif anggota ASG.
- `GET /scaling-groups/{id}/history` — Audit trail aktivitas scaling otomatis/manual.

### CLI Usage
```bash
cloudx scaling-group list
cloudx scaling-group create my-asg --image <image_id> --flavor <flavor_id> --network <net_id> --min-instances 1 --max-instances 5 --target-cpu 70
cloudx scaling-group scale-out <asg_id>
cloudx scaling-group scale-in <asg_id>
cloudx scaling-group instance-list <asg_id>
cloudx scaling-group history <asg_id>
```

---

## 3. VPC Peering & BGP Dynamic Routing (`networking-api` - Port 8001)

Perluasan kapabilitas OVN/OVS networking untuk menghubungkan 2 VPC/Router terpisah tanpa NAT, serta koordinasi dynamic route advertisement via BGP protocol.

### VPC Peering Endpoints
- `POST /routers/{requester_id}/peering` — Buat VPC Peering antara 2 Router tenant (`accepter_router_id`).
- `GET /vpc-peerings` & `GET /vpc-peerings/{id}` — Daftar dan detail koneksi VPC Peering.
- `DELETE /vpc-peerings/{id}` — Hapus VPC Peering dan teardown OVN patch port.

### BGP Dynamic Routing Endpoints
- `POST /bgp-peers` & `GET /bgp-peers` — Konfigurasi BGP Peer (Local AS, Remote AS, Peer IP, Advertised Prefixes).
- `GET /bgp-peers/{id}` & `DELETE /bgp-peers/{id}` — Detail dan hapus BGP Peer.
- `POST /bgp-peers/{id}/sync-config` — Sinkronkan konfigurasi BGP router daemon (FRR / Quagga).

### CLI Usage
```bash
# VPC Peering
cloudx vpc-peering list
cloudx vpc-peering create my-peering --requester <router1_id> --accepter <router2_id>
cloudx vpc-peering show <peering_id>
cloudx vpc-peering delete <peering_id>

# BGP Peers
cloudx bgp-peer list
cloudx bgp-peer create my-bgp --router <router_id> --peer-ip 192.168.255.1 --local-as 64513 --remote-as 64512 --advertised-prefix 10.0.0.0/16
cloudx bgp-peer sync-config <bgp_peer_id>
```
