# Console

Serial console. The command runs the local `virsh` against the local libvirt (`qemu:///system`), so run it on the compute node that hosts the server:

```bash
cloudx server console vm1
```

Exit `virsh console` with `Ctrl+]`.

Console metadata, including the browser link:

```bash
cloudx server console-url vm1
```

The answer holds the `virsh` command, the VNC port libvirt assigned (`vnc`) and `novnc_url`, a link to the browser console. `novnc_url` is `null` when the compute worker could not say where the console is (the worker did not answer, or the guest has no VNC port).

## How the browser console works

Every compute node runs its own noVNC proxy: the unit `cloudx-novnc-proxy` starts `websockify` on port 6080 and serves the noVNC web client from `/usr/share/novnc`. The guest's VNC server stays bound to `127.0.0.1` on the node; only the proxy is reachable from outside.

`GET /servers/{ref}/console-url` and `POST /servers/{ref}/remote-consoles` (the second one needs the server to be `ACTIVE`) return a link of this form:

```
http://<console_proxy_host>:6080/vnc.html?autoconnect=1&path=websockify%3Ftoken=<token>
```

1. compute-api asks the compute worker of the server's node for the VNC port and for that node's name and proxy address.
2. compute-api signs a token (JWT, HS256, with a key derived from `auth_secret_key`, so a console token is never accepted as a login token and the other way round) that says: console, this server, this node, this VNC port and the libvirt domain that owns it. It is valid for 300 seconds.
3. The browser opens the link on the compute node, and noVNC connects to the proxy with the token.
4. The proxy checks the signature, the expiry and that the token names its own node (`COMPUTE_HOST`), then connects to `127.0.0.1:<port>`. A token for another node, an expired one or an ordinary login token is refused. It then asks libvirt whether the token's domain still has its VNC graphics on the token's port, and refuses the token if not.

A console token is bound to the VM's libvirt domain and port. It stops working when the VM stops or is deleted, or when its VNC port changes (libvirt reuses a freed port for another VM, and the old link must not open that one), even within the 300 seconds. Reload the page for a new link.

The token is checked when the connection is opened. A console page that stays open for more than 5 minutes before it connects has to be reloaded to get a new link.

The link is plain `http`. The proxy has no TLS.

### What a compute node needs

- The `novnc` apt package. The `cloudx-compute-worker` package depends on it, so nothing extra is needed on a node installed from the `.deb` packages ([deb-install.md](deb-install.md)). On a node installed from the tarball, run `sudo apt install novnc` ([tarball-install.md](tarball-install.md)). The unit has `ConditionPathExists=/usr/share/novnc`: without the package it is skipped without an error, and nothing listens on port 6080.
- `websockify` in `/opt/cloudx/venv` (part of the package venv; `install-systemd.sh` and `requirements.txt` install it for the tarball).
- In the `.deb` packages the shipped `cloudx-compute-worker.service` has `Wants=cloudx-novnc-proxy.service`, so the proxy starts when the worker starts; the package ships the proxy unit but does not enable it on its own. On a tarball node the unit in [tarball-install.md](tarball-install.md) section 4.4 has the same line and its start command enables the proxy; a worker unit written by hand without them leaves nothing listening on port 6080.
- Port 6080/tcp reachable from the browsers of your users, on every compute node.
- The same `auth_secret_key` as the control plane (it comes with the `/etc/cloudx/cloudx.conf` copied from the controller).
- The same `compute_host` as the compute worker. The proxy reads `/etc/cloudx/compute-worker/compute-worker.conf` like the worker does and falls back to `NODE_NAME` and then `cloudx-compute-01`, the same chain as the worker. If you set `COMPUTE_HOST` as an `Environment=` line in the worker's unit (the tarball layout), set it for `cloudx-novnc-proxy` too (`sudo systemctl edit cloudx-novnc-proxy`), or the proxy refuses every token.

### `console_proxy_host`

The link points at the address a browser uses to reach port 6080 on the node. By default that is the node's `COMPUTE_HOST`, which is often a short name that your users' browsers cannot resolve. Set the reachable name or IP in `/etc/cloudx/compute-worker/compute-worker.conf` on each compute node:

```ini
[DEFAULT]
console_proxy_host = 203.0.113.10
```

Restart the worker (`sudo systemctl restart cloudx-compute-worker`); the worker reports this address to compute-api, and the proxy does not read it. `etc/cloudx/cloudx.conf.sample` has the line. The old `NOVNC_BASE_URL` setting no longer exists and is not read.

## Web console (Dashboard)

1. Open the CloudX Dashboard at `http://localhost:8080`.
2. Navigate to **Compute > Instances** and click **Connect** on any ACTIVE instance (or open `/console/:id`).
3. The noVNC client opens inside the page, loaded from port 6080 of the server's compute node. The browser of the person using the dashboard must reach that port.

## Troubleshooting

| What you see | Cause |
|---|---|
| The console page shows an error about the server state (HTTP 409) | `remote-consoles` needs the server to be `ACTIVE`. |
| The console page says "Console URL not available" | The compute worker did not return a VNC port, so the link is `null` (also `novnc_url` in `cloudx server console-url`). Check that the worker runs on the server's node. |
| The browser cannot connect to `<host>:6080` (connection refused or time-out) | The proxy is not running (`systemctl status cloudx-novnc-proxy`; with `/usr/share/novnc` missing the unit is skipped), or a firewall blocks port 6080, or `console_proxy_host` names an address the browser cannot reach. |
| The noVNC page loads and then cannot connect | The token expired (reload the page for a new link), or the proxy rejected it: `compute_host` or `auth_secret_key` on the node differs from what compute-api and the worker use. The proxy's log is `journalctl -u cloudx-novnc-proxy`. |
| The link has a node name that does not resolve | `console_proxy_host` is not set on that node. |
