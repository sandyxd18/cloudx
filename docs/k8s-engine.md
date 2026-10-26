# K8s Engine (K3s)

`k8s-engine` creates real K3s clusters on CloudX VMs. Parity with OpenStack Magnum is in features and behaviour only, not 1:1.

## Flow

1. `cloudx cluster create` creates a cluster row with status `CREATING`, along with a random `join_token` and `admin_token`. The API only records the intent.
2. `cloudx-k8s-engine-worker` (controller, polls every 5 seconds) creates the master VM through the compute API with cloud-config `user_data`.
3. The master VM runs `k3s server`, waits for `/readyz`, then calls the callback `POST /internal/k8s-engine/clusters/{id}/ready` on the controller.
4. Once the master is ready, the worker creates `worker_count` worker VMs. Each worker runs `k3s agent` and calls the same callback.
5. All nodes ready: status `ACTIVE`. Past `READY_TIMEOUT` (900 seconds): status `ERROR` with a `status_reason`.

A callback is used because the controller and compute have no route to the tenant network. The VM must be able to reach `callback_url`.

## K3s image

K3s is baked once into a Glance image, so VMs do not need internet.

```bash
# on a compute node, as root
sudo scripts/build-k3s-image.sh --base-image /var/lib/cloudx/images/ubuntu-24.04.img
sudo scripts/build-k3s-image.sh --base-image ubuntu.img --dry-run   # only prints the steps
```

The script copies the `k3s` binary and the airgap tarball into a copy of the image, then uploads it as `k3s-ubuntu`. The base image is not changed. The default K3s version is `v1.30.2+k3s1`.

## Worker configuration

`/etc/cloudx/k8s-engine-worker/k8s-engine-worker.conf`:

```ini
[DEFAULT]
k8s_engine_api_url = http://127.0.0.1:8015
compute_api_url = http://127.0.0.1:8003
callback_url = http://<controller-ip-reachable-from-the-vms>:8015
vm_image = k3s-ubuntu
master_flavor = m1.medium
worker_flavor = m1.small
ready_timeout = 900
```

`internal_token` is taken from `/etc/cloudx/cloudx.conf`.

## Usage

```bash
cloudx k8s-template create tpl-k3s --image k3s-ubuntu
cloudx cluster create demo --template <tpl-id> --network <net-id> --workers 1
cloudx cluster list            # columns status, master_ip, status_reason
cloudx cluster scale <id> --workers 2
cloudx cluster kubeconfig <id> > kubeconfig
cloudx cluster delete <id>     # status DELETING; the worker deletes all VMs and then the row
```

`scale` is only accepted when `ACTIVE` (409 otherwise). `kubeconfig` only when `ACTIVE` or `SCALING` and once `master_ip` is set.

## Troubleshooting

- A cluster stuck in `CREATING`: check `cloudx cluster show <id>` (`status_reason`), `journalctl -u cloudx-k8s-engine-worker`, and whether the VMs can reach `callback_url`.
- On the VM: `/var/log/cloudx-k3s-boot.log` and `/var/log/k3s.log`.
- `ERROR`: the recorded VMs are cleaned up when the cluster is deleted; delete the cluster and create it again.

## Limitations

- No control plane HA, no version upgrades, no autoscaler.
- The K3s image is built by hand; the K3s version is fixed by the image.
- Tokens are stored in plaintext in the DB; the kubeconfig uses a bearer token with `insecure-skip-tls-verify`.
- `master_flavor`, `worker_flavor` and `vm_image` come from the worker configuration, not from the cluster template.
- Cluster VMs are created through an internal call, so they land in the `admin` project, not the cluster owner's project (quota and billing follow the admin project).
- There is no timeout for `SCALING` (no scale start time is stored).
- The `ready` callback matches nodes by role and IP; the first node that is not ready is used when the IP is not yet known.
- The existing CSI/CCM are not yet connected to a real cluster.
- Lab size: 1 master + 1-2 small workers (compute with 8 GB).
- Depends on VMs being able to reach the k8s-engine API on the controller.
