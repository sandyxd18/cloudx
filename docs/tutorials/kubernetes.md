# Tutorial: a Kubernetes cluster (K3s) on CloudX VMs

You will bake K3s into an Ubuntu image, describe a cluster template, create a cluster with one master and one worker, fetch its kubeconfig, scale it and delete it. About an hour, most of it spent building the image and waiting for the VMs.

> **Status: not re-run on the current hardware.** The K3s engine was run live in the 0.7.19 development lab: one master and one worker reached `ACTIVE`, both nodes were Ready, scaling to two workers and back to zero worked, and deleting the cluster left nothing behind. It has not been run since, because the three machines of the current lab have 3.7 GB of RAM each and a cluster needs a 2 GB master VM plus a 1 GB worker VM next to the control plane. The commands below are the ones in [../k8s-engine.md](../k8s-engine.md) and `scripts/build-k3s-image.sh`; if a step fails, please open an issue with the output.

## What you need

- A working cloud with at least one compute node that has room for a 2 GB and a 1 GB VM: [multi-node.md](multi-node.md) or the [quickstart](../quickstart.md).
- An Ubuntu 24.04 cloud image (`ubuntu-24.04-server-cloudimg-amd64.img`) on the compute node, to bake K3s into. It is copied, not changed.
- On the compute node, as root: `qemu-nbd`, `qemu-img`, `curl` and the `cloudx` CLI logged in as admin. The image build downloads the K3s binary and its air-gap image bundle once; the VMs themselves do not need internet afterwards.
- `kubectl` on your workstation.
- A network route from the tenant network to the controller, because the VMs report back to it (section 3).

## 1. Install the engine

On the controller:

```bash
cd ~/debs
sudo apt install ./cloudx-k8s-engine-api_*.deb ./cloudx-k8s-engine-worker_*.deb
```

The packaged `/etc/cloudx/k8s-engine-api/k8s-engine-api.conf` has its own `database_url` (database `cloudx_k8s_engine`) with the default password; if you changed PostgreSQL's password, change it there too and keep the file `640 root:cloudx`. Then, as for any new service ([autoscaling.md](autoscaling.md) section 1 shows the sequence): add `k8s_engine_api_url = http://<controller>:8015` to `/etc/cloudx/auth-api/auth-api.conf`, run `sudo cloudx-manage db sync`, `sudo cloudx-manage catalog sync`, restart the units and log in again so the CLI sees the new service.

## 2. Build the K3s image

On the compute node, as root, from a checkout (or from the release tarball) that contains `scripts/build-k3s-image.sh`:

```bash
sudo scripts/build-k3s-image.sh --base-image /var/lib/cloudx/images/ubuntu-24.04.img --dry-run   # prints the steps
sudo scripts/build-k3s-image.sh --base-image /var/lib/cloudx/images/ubuntu-24.04.img
```

It copies the K3s binary (`v1.30.2+k3s1` by default, `--k3s-version` to change it) and the air-gap tarball into a copy of the image and uploads the result as the image `k3s-ubuntu`. Check with `cloudx image list`.

## 3. Configure the worker

`/etc/cloudx/k8s-engine-worker/k8s-engine-worker.conf` on the controller:

```ini
[DEFAULT]
k8s_engine_api_url = http://127.0.0.1:8015
compute_api_url = http://127.0.0.1:8003
callback_url = http://<controller address that the VMs can reach>:8015
vm_image = k3s-ubuntu
master_flavor = medium
worker_flavor = small
ready_timeout = 900
```

The flavor names are the ones in `cloudx flavor list`. `callback_url` matters: each VM calls it when K3s is ready. A VM on a tenant network behind a router can reach the controller only if the router has an external gateway and the controller's address is routable from the provider network. If clusters stay `CREATING` until they time out, this is the first thing to check.

```bash
sudo systemctl restart cloudx-k8s-engine-worker
```

## 4. Create the cluster

```bash
cloudx k8s-template create tpl-k3s --image k3s-ubuntu
cloudx cluster create demo --template <template id> --network demo-net --masters 1 --workers 1
cloudx cluster list                       # CREATING -> ACTIVE, with master_ip and status_reason
```

What happens: the worker creates the master VM with a cloud-config that starts `k3s server`; the master waits for `/readyz`, then calls the callback; the worker then creates the worker VMs, which run `k3s agent` and call back. When every node has reported, the cluster is `ACTIVE`. If it is not ready after `ready_timeout` seconds it becomes `ERROR` with the reason in `status_reason`.

## 5. Use it

```bash
cloudx cluster kubeconfig <cluster id> > kubeconfig
KUBECONFIG=$PWD/kubeconfig kubectl get nodes
```

The kubeconfig uses the cluster's admin bearer token and `insecure-skip-tls-verify`. It points at the master's `master_ip` (see `cloudx cluster list`), so run `kubectl` from a machine that can reach that address.

## 6. Scale and delete

```bash
cloudx cluster scale <cluster id> --workers 2     # only while the cluster is ACTIVE
cloudx cluster delete <cluster id>                # DELETING; the worker deletes all its VMs and then the row
```

## If it does not become ACTIVE

- `cloudx cluster show <id>`: `status_reason` says what the worker saw.
- `journalctl -u cloudx-k8s-engine-worker` on the controller.
- On a VM (console or SSH): `/var/log/cloudx-k3s-boot.log` and `/var/log/k3s.log`.
- Whether the VM can reach `callback_url` (`curl` it from the VM).
- Delete the cluster and create it again; an `ERROR` cluster's VMs are cleaned up when you delete it.

## Limits

No control plane HA, no version upgrades, no cluster autoscaler. The K3s version is whatever the image holds. Tokens are stored in plaintext in the database. The master and worker flavors and the image come from the worker's configuration, not from the template. The cluster's VMs are created in the `admin` project, so quota and billing follow that project. The complete list is in [../k8s-engine.md](../k8s-engine.md).
