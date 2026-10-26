# Container (Zun-like)

Single containers on compute nodes, each with its own IP on a tenant network. Clusters are in `k8s-engine`.

## Architecture

- `container-api` (controller, port 8018): tables `ct_containers` and `ct_hosts`.
- `container-worker` (every compute node, root): polls `GET /internal/ct-agent/<host>` every 5 seconds, reconciles with Docker, then reports to `POST /internal/ct-agent/<host>/report` (status, error, the last 200 log lines).
- The host is chosen at create: a live node (heartbeat < 30 seconds) with the fewest containers. None alive: 503.
- Networking: containers run with `--network none`. The API creates an OVN port through networking-api (security groups apply), and the worker plugs an OVS internal port on `br-int` into the container's netns. After a restart the plumbing is redone because the PID changes. No NAT.
- Docker is forced to `iptables:false`, `bridge:none` (`/etc/docker/daemon.json`) so the router NAT is not touched.

## Security

No `--privileged`, host mounts or host network. Always `--cap-drop ALL` (only CHOWN, DAC_OVERRIDE, FOWNER, SETUID, SETGID, NET_BIND_SERVICE are added back), `no-new-privileges`, `--pids-limit 256`, and CPU (0.1-4) and memory (64-4096 MB) limits are required. The image is validated with a regex, and the command is passed as an argument list (no shell).

## Configuration

Controller: `container_api_url`. Every worker node, in `/etc/cloudx/container-worker/container-worker.conf` (with `[DEFAULT]`): `container_api_url`, `container_host` (the same as `node_name`). `install-systemd.sh` installs Docker only on nodes that have `container_host`.

## CLI

```
cloudx container create web --image nginx:1.27 --network <net> --cpu 0.5 --memory 128 --env A=1 --command sleep --command 10
cloudx container list | show | start | stop | restart | logs | delete
```

## Limits

Exec, block-store volumes, published ports and floating IPs, image builds, private registries, restart policy, resize and an interactive console do not exist yet. Pulling an image needs internet from the node. A new container has no network for a few seconds until the worker plugs it in, so a process that uses the network right at start can fail.
