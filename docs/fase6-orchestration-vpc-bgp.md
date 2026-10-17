# 🚀 Phase 6 — Multi-Node Compute, Auto-Scaling & VPC Peering/BGP

This document describes the architecture, API endpoints, and operation of the Phase 6 features: the **Multi-Node Scheduler (Placement extension)**, **Auto-Scaling Group Orchestration (Heat/Senlin-like)**, and **VPC Peering & BGP Dynamic Routing (Networking extension)**.

---

## 📋 Service Overview

| Service | Port | Database | Description |
|---------|------|----------|-----------|
| `placement-api` | 8006 | `cloudx` | Dynamic node heartbeat, multi-node compute node discovery, scheduling candidate resolution (`select_destination`). |
| `orchestration-api` | 8013 | `cloudx_orchestration` | Engine that manages Auto-Scaling Groups (ASG): automatic metric evaluation, scale-out and scale-in. |
| `orchestration-worker` | N/A | `cloudx_orchestration` | Background worker that evaluates telemetry metric thresholds against ASG policies every 30 seconds. |
| `networking-api` | 8001 | `cloudx` | Inter-router VPC peering through OVN logical patch ports & BGP dynamic routing neighbour management. |

---

## 1. Multi-Node Scheduler (`placement-api` - Port 8006)

Lets a CloudX cluster manage many compute nodes (hypervisors), monitor node health through heartbeats, and choose the optimal compute destination when creating a VM or during live migration.

### Main API Endpoints
- `POST /resource_providers/{rp_uuid}/heartbeat` — Compute node heartbeat pings.
- `GET /compute_nodes` — List of all connected hypervisors / compute nodes with their CPU, memory, and disk capacity.
- `GET /allocation_candidates` — Search for compute nodes that satisfy a VM flavor's criteria.
- `POST /scheduler/select_destination` — Algorithm that selects the best compute node based on load and resource availability.

### CLI Usage
```bash
cloudx placement summary
cloudx placement node-list
```

---

## 2. Auto-Scaling Group Orchestration (`orchestration-api` - Port 8013)

A VM cluster orchestrator that automatically adds (*scale-out*) or removes (*scale-in*) VM instances based on the average telemetry load (for example: target CPU > 70%).

### Main API Endpoints
- `GET /scaling-groups` & `POST /scaling-groups` — Create and list Auto-Scaling Groups.
- `GET /scaling-groups/{id}` & `PATCH /scaling-groups/{id}` — Details & capacity updates (min_instances, max_instances, target_cpu_percent, cooldown_seconds).
- `POST /scaling-groups/{id}/scale-out` — Manually trigger a scale-out (add 1 VM instance).
- `POST /scaling-groups/{id}/scale-in` — Manually trigger a scale-in (terminate 1 VM instance).
- `GET /scaling-groups/{id}/instances` — List of active VMs that belong to the ASG.
- `GET /scaling-groups/{id}/history` — Audit trail of automatic/manual scaling activity.

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

Extends the OVN/OVS networking capabilities to connect two separate VPCs/routers without NAT, and to coordinate dynamic route advertisement through the BGP protocol.

**Behavior & Limitations**:
- Same-project only (enforced via project scoping / ownership checks).
- Static routing via OVN logical router patch ports and static routes (`lr-route-add`).
- BGP peers run a real BIRD session; see [bgp-dynamic-routing.md](bgp-dynamic-routing.md). VPC peering itself uses static routes only.
- Transit CIDRs: automatically allocated /30 subnets from `100.100.0.0/16` (legacy rows default to `100.100.0.0/30`).
- Overlapping router subnets are refused with HTTP 409.

### VPC Peering Endpoints
- `POST /routers/{requester_id}/peering` — Create a VPC peering between two tenant routers (`accepter_router_id`).
- `GET /vpc-peerings` & `GET /vpc-peerings/{id}` — List and show VPC peering connections.
- `DELETE /vpc-peerings/{id}` — Delete a VPC peering and tear down the OVN patch ports.

### BGP Dynamic Routing Endpoints
- `POST /bgp-peers` & `GET /bgp-peers` — Configure a BGP peer (Local AS, Remote AS, Peer IP, Advertised Prefixes).
- `GET /bgp-peers/{id}` & `DELETE /bgp-peers/{id}` — Show and delete a BGP peer.
- `POST /bgp-peers/{id}/sync-config` — Return the rendered BIRD configuration (password masked).

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
