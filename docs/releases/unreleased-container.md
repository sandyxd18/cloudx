# CloudX (version to be assigned) - Container service

New `container-api` (port 8018) and `container-worker`: single containers (Zun-like) on compute nodes, each with its own address on a tenant network.

## Highlights

- Docker runtime, `--network none`; the container gets an OVN port, so tenant security groups apply and there is no NAT.
- Create, list, show, start, stop, restart, logs (last 200 lines) and delete through API, CLI (`cloudx container`) and dashboard (Containers).
- Hardened by default: all capabilities dropped except a small set, `no-new-privileges`, pids limit, mandatory CPU and memory limits, no host mounts, no privileged mode.

## Upgrading

1. Deploy, then run `cloudx-db-migrate container` on the controller and enable `cloudx-container-api`.
2. On every node that should run containers add `container_host = <node_name>` (and `container_api_url`) to `/etc/cloudx/container-worker/container-worker.conf` (needs a `[DEFAULT]` header), run `install-systemd.sh` (installs Docker with `iptables:false`, `bridge:none`) and enable `cloudx-container-worker`.
3. Add `container_api_url` to `cloudx.conf` and proxy `CONTAINER_API_URL` for the dashboard if it is not on the default.

## Limitations

- Not implemented: exec, block-store volumes, published ports and floating IPs, image build, private registries, restart policy, resize, interactive console.
- Images are pulled from public registries by the node, so it needs internet access.
- The container starts without a network; the worker attaches it within a poll interval (about 5 s).
- No scheduling beyond "fewest containers"; no quota.