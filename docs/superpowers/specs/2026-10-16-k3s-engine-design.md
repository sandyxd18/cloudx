# Real K8s engine (K3s) - design

Status: approved by the user (chat, 2026-10-16).

## Goal
`services/k8s_engine` is only a CRUD stub today: a cluster is `ACTIVE` at once, with no VM and a fake kubeconfig. This phase creates real K3s clusters on CloudX VMs.

## Decisions
- Real K3s on CloudX VMs (master + workers).
- K3s is baked into a Glance image (`k3s-ubuntu`); the VMs do not need internet.
- Readiness is detected through a callback from the VM (the controller has no route to the tenant network; result of the spike).

## Components
1. `scripts/build-k3s-image.sh`: on a compute node, take an Ubuntu cloud image, mount it with `qemu-nbd`, copy the K3s binary + the air-gap tarball + the installer, upload to Glance as `k3s-ubuntu`. Run by hand once.
2. `k8s-engine-api`: template/cluster CRUD stays; create/scale/delete only change `status` and `desired` (CREATING, SCALING, DELETING). Internal endpoint `POST /internal/clusters/{id}/ready` (InternalAuth, body: `role`, `node_name`, `kubeconfig_ca`).
3. `k8s-engine-worker` (compute or controller; `orchestration_worker` pattern, polling every 5 s, `InternalAuth`): reconciliation.
   - Master: `POST compute /servers` with `user_data` that runs `k3s server --token-file ... --tls-san <ip>`, then calls the ready callback.
   - Workers: created after the master is `ready`, `k3s agent --server https://<master_ip>:6443 --token <join>`.
   - Scale: add/remove worker VMs up to `worker_count`.
   - Delete: delete every VM of the cluster; leftover VMs are cleaned up after a partial failure.
   - `ACTIVE` when the master is ready and the number of ready workers equals the requested number; timeout -> `ERROR` + `status_reason`.
4. New table `k8s_cluster_nodes` (id, cluster_id, server_id, role, ip, ready) and columns on `k8s_clusters`: `join_token`, `admin_token`, `status_reason`, `master_ip`. Migration `k8s_engine_0002`.
5. Kubeconfig: server `https://<master_ip>:6443`, bearer `admin_token`; `insecure-skip-tls-verify` as a recorded limitation.

## Tests
- Unit: the rendered `user_data` (master/agent, no token in logs), the state machine, scale up/down, cleanup after a partial failure, the internal callback through `TestClient`. A mutation check per invariant.
- Live (`cloudx-net-ops`): build the image, create a cluster with 1 master + 1 worker, wait for `ACTIVE`, `kubectl get nodes` through the kubeconfig from a VM/host that can reach it, scale, delete, clean state.

## Limitations
- No control plane HA, no version upgrades, no autoscaler.
- The K3s image is built by hand; the K3s version is tied to the image.
- Tokens are stored in plaintext in the DB; TLS without verification.
- The existing CSI/CCM are not yet connected to a real cluster.
- Lab size: 1 master + 1-2 small workers (compute with 8 GB).
- Depends on the VMs being able to reach the k8s-engine API on the controller.
