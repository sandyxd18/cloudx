# Tutorial: a highly available control plane with LVM and Ceph volumes

You will build one CloudX cloud on eight nodes: three controllers that run every service and can lose one member, three Ceph nodes, and two compute nodes that serve LVM volumes next to Ceph RBD volumes. CloudX does not ship control plane HA. The pieces below (keepalived, HAProxy, Patroni, a RabbitMQ cluster, OVN raft) are distro packages around unchanged CloudX packages. About a day, mostly waiting for packages and Ceph.

> **Status: run once, on 2026-10-28, with CloudX 1.0.0.** Everything below was done on the lab described in section 1, and the numbers in section 9 are what was measured there, not promises. What was **not** tested is listed in section 10 (among others `ha-watchdog` and TLS between services). Where a step did not work the way you would expect, this page says so (sections 7 and 10). If a step fails for you, please open an issue with the output.

## 1. What you need

| Nodes | Role | Resources used in the lab |
|---|---|---|
| `ctl01-03` | every CloudX service in section 3.4, etcd, Patroni + PostgreSQL 16, RabbitMQ, OVN central, HAProxy, keepalived | 4 vCPU, 8 GB, 40 GB disk |
| `ceph01-03` | `cephadm` cluster: 3 mon, 2 mgr, 3 OSD, 2 MDS | 2 vCPU, 8 GB, 32 GB + empty 50 GB disk |
| `comp01-02` | compute, the OVN gateway chassis, LVM volumes | 4 vCPU, 8 GB, 32 GB + empty 30 GB disk, `/dev/kvm` |

Ubuntu 24.04 on every node. Networks (the bridges are Proxmox bridges; use your own names):

| Network | CIDR | Used for |
|---|---|---|
| management | 172.20.0.0/16 | the VIP, clients, compute to controllers, the Internet |
| cluster-ctl | 192.168.29.0/29 | controller to controller: etcd, Patroni, RabbitMQ, OVN raft |
| public-ceph | 192.168.26.0/28 (MTU 9000) | Ceph clients and monitors: every node |
| private-ceph | 192.168.27.0/29 (MTU 9000) | OSD replication |
| tunnel | 192.168.25.0/29 | Geneve between the compute nodes |
| iSCSI | 192.168.28.0/29 | LVM volumes between the compute nodes |
| provider | `ens19` on the compute nodes | `br-ex`, floating IPs |

The controllers have no tunnel or provider NIC and need none: they run only `ovn-central`, and the gateway chassis are the compute nodes. Reserve one management address for the VIP (the lab used `172.20.0.151`) and make sure DHCP will not hand it out; reserve the nodes' own addresses too.

Write the node names into `/etc/hosts` on every node: `ctlNN` and `compNN` to their management address, `ctlNN-c` to the cluster-ctl address, `cephNN` to the public-ceph address. If your image has `manage_etc_hosts: true` in cloud-init, edit `/etc/cloud/templates/hosts.debian.tmpl` (and delete its `127.0.1.1` line), otherwise cloud-init rewrites `/etc/hosts` at every boot.

Download the 47 `.deb` files of the release into `~/cloudx-debs` on every node, as in [deb-install.md](../deb-install.md) section 3, and check `SHA256SUMS`. Check the jumbo path before Ceph: `ping -c3 -M do -s 8972 <peer>` on public-ceph and private-ceph must have no loss.

## 2. Ceph and the shared image store

Follow [ceph.md](ceph.md) up to the pools, with three differences:

- Bootstrap with the image pinned to the client version: `sudo cephadm --image quay.io/ceph/ceph:v19.2.3 bootstrap --mon-ip 192.168.26.4 --cluster-network 192.168.27.0/29 --ssh-user ubuntu --skip-dashboard --skip-monitoring-stack`. On `ceph01`. On an earlier six-node lab the moving `v19` tag gave 19.2.7, whose cephx keys Ubuntu's 19.2.3 clients could not read; not re-checked here.
- Add the other two hosts with their public-ceph address (`ceph orch host add ceph02 192.168.26.5 --labels _admin`), `mon` on all three, `mgr` on two, one OSD on each empty disk.
- Also create a CephFS and a client key for it:

```bash
sudo ceph fs volume create cloudfs                       # creates the pools and the MDS daemons
sudo ceph fs authorize cloudfs client.cloudfs / rw
sudo ceph auth get-key client.cloudfs | sudo tee /etc/ceph/cloudfs.secret
sudo chmod 600 /etc/ceph/cloudfs.secret      # then copy it to the controllers (tee creates it world-readable)
```

`image-api` has only a `file` and a MinIO backend, so with three controllers the image files must live on shared storage. Mount the CephFS on `/var/lib/cloudx/images` on all three controllers (`ceph-common` installed, `ceph.conf` in place):

```bash
sudo mkdir -p /var/lib/cloudx/images
echo '192.168.26.4,192.168.26.5,192.168.26.6:/ /var/lib/cloudx/images ceph name=cloudfs,secretfile=/etc/ceph/cloudfs.secret,_netdev,noatime 0 0' | sudo tee -a /etc/fstab
sudo mount /var/lib/cloudx/images
```

Check that a file written on one controller is read on the other two. The `cloudx` user must have the same numeric uid on all controllers (it was 999 on a clean install); the image files are owned by that uid.

Copy `ceph.conf` and `ceph.client.cloudx.keyring` from `ceph01` to the controllers and the compute nodes (`ceph-common` there; `qemu-utils` on the controllers). Gate: `ceph -s` is `HEALTH_OK` with 3 OSD and every daemon on 19.2.3.

## 3. The data tier and the entry point

All of this runs on `ctl01-03`. Install `etcd-server etcd-client patroni postgresql-16 rabbitmq-server ovn-central openvswitch-switch haproxy keepalived memcached`, then drop the default cluster (`sudo pg_dropcluster 16 main --stop`) and set `net.ipv4.ip_nonlocal_bind=1` in `/etc/sysctl.d/`.

### 3.1 etcd and Patroni (PostgreSQL)

etcd listens on the cluster-ctl address only (`/etc/default/etcd`, plain HTTP on a private /29):

```ini
ETCD_NAME="ctl01"                       # per node
ETCD_DATA_DIR="/var/lib/etcd/cloudx-ha"
ETCD_LISTEN_PEER_URLS="http://192.168.29.1:2380"
ETCD_LISTEN_CLIENT_URLS="http://192.168.29.1:2379,http://127.0.0.1:2379"
ETCD_INITIAL_ADVERTISE_PEER_URLS="http://192.168.29.1:2380"
ETCD_ADVERTISE_CLIENT_URLS="http://192.168.29.1:2379"
ETCD_INITIAL_CLUSTER="ctl01=http://192.168.29.1:2380,ctl02=http://192.168.29.2:2380,ctl03=http://192.168.29.3:2380"
ETCD_INITIAL_CLUSTER_STATE="new"
ETCD_INITIAL_CLUSTER_TOKEN="cloudx-ha"
```

`/etc/patroni/config.yml` (mode 600, owner `postgres`; the lab started `ctl01` first and the others after it was the leader):

```yaml
scope: cloudx
name: ctl01
restapi: {listen: 192.168.29.1:8008, connect_address: 192.168.29.1:8008}
etcd3: {hosts: "192.168.29.1:2379,192.168.29.2:2379,192.168.29.3:2379"}
bootstrap:
  dcs:
    ttl: 30
    loop_wait: 10
    retry_timeout: 10
    maximum_lag_on_failover: 1048576
    postgresql:
      use_pg_rewind: true
      parameters: {max_connections: 300, shared_buffers: 256MB, wal_level: replica, hot_standby: "on", wal_keep_size: 512MB, max_wal_senders: 10, max_replication_slots: 10}
  initdb: [{encoding: UTF8}, data-checksums]
  pg_hba:
    - local all all peer
    - host all all 127.0.0.1/32 scram-sha-256
    - host replication replicator 192.168.29.0/29 scram-sha-256
    - host all all 192.168.29.0/29 scram-sha-256
postgresql:
  listen: 192.168.29.1:5432
  connect_address: 192.168.29.1:5432
  data_dir: /var/lib/postgresql/16/cloudx
  bin_dir: /usr/lib/postgresql/16/bin
  authentication:
    replication: {username: replicator, password: "<random>"}
    superuser: {username: postgres, password: "<random>"}
  parameters: {unix_socket_directories: /var/run/postgresql}
```

Gate: `sudo -u postgres patronictl -c /etc/patroni/config.yml list` shows one Leader and two streaming Replicas with lag 0. On the leader create the user and the databases of [quickstart.md](../quickstart.md) step 2, with a **random** password for `cloudx` (see the warning in 3.5).

### 3.2 RabbitMQ

Name the nodes after the cluster-ctl address so Erlang distribution uses that network, and bind AMQP to it so HAProxy can own `VIP:5672`:

```ini
# /etc/rabbitmq/rabbitmq-env.conf
NODENAME=rabbit@ctl01-c
# /etc/rabbitmq/rabbitmq.conf
listeners.tcp.1 = 192.168.29.1:5672
cluster_partition_handling = pause_minority
```

Start `ctl01`, copy `/var/lib/rabbitmq/.erlang.cookie` to the others (mode 400, owner `rabbitmq`), then on `ctl02` and `ctl03`: `rabbitmqctl stop_app && rabbitmqctl join_cluster rabbit@ctl01-c && rabbitmqctl start_app`. Then:

```bash
sudo rabbitmqctl add_user cloudx '<random>' && sudo rabbitmqctl set_user_tags cloudx administrator
sudo rabbitmqctl set_permissions -p / cloudx ".*" ".*" ".*" && sudo rabbitmqctl delete_user guest
sudo rabbitmqctl set_policy ha-all '^' '{"ha-mode":"all","ha-sync-mode":"automatic"}' --apply-to queues
```

CloudX declares durable classic queues over one connection URL, so mirroring needs no code change. Gate: `cluster_status` lists three running nodes and no partition.

### 3.3 OVN raft

The Ubuntu units read `OVN_CTL_OPTS` from `/etc/default/ovn-central`. Client ports go on the management address (the compute nodes connect there), raft on cluster-ctl. `ctl01` creates the clusters, the others join through it:

```sh
# ctl01
OVN_CTL_OPTS="--db-nb-addr=172.20.0.138 --db-nb-create-insecure-remote=yes --db-nb-cluster-local-addr=192.168.29.1 --db-sb-addr=172.20.0.138 --db-sb-create-insecure-remote=yes --db-sb-cluster-local-addr=192.168.29.1 --ovn-northd-nb-db=tcp:172.20.0.138:6641,tcp:172.20.0.145:6641,tcp:172.20.0.146:6641 --ovn-northd-sb-db=tcp:172.20.0.138:6642,tcp:172.20.0.145:6642,tcp:172.20.0.146:6642"
# ctl02/ctl03: their own addresses, plus
#   --db-nb-cluster-remote-addr=192.168.29.1 --db-sb-cluster-remote-addr=192.168.29.1
```

Delete `/var/lib/ovn/ovn{nb,sb}_db.db` first (a standalone database cannot become a cluster member) and start `ovn-ovsdb-server-nb`, `ovn-ovsdb-server-sb`, `ovn-northd`, `ctl01` first.

**The two `--ovn-northd-*-db` options are not optional.** Without them each `ovn-northd` reads only its local socket and only accepts a cluster leader, so northd works only on the node that leads *both* the NB and the SB database. Everything looked fine until the two leaders moved apart; then no northd translated NB changes. And **`systemctl restart ovn-northd` also stops the local NB and SB database servers** on Ubuntu 24.04 (24.03.8). When you change these options, go node by node and restart `ovn-ovsdb-server-nb ovn-ovsdb-server-sb ovn-northd` on one node, wait until `cluster/status` is healthy, then the next. Restarting all three at once took the OVN control plane down for about 1.5 minutes in the lab (the VMs' network kept working).

`--db-nb-create-insecure-remote=yes` opens `6641` and `6642` on the management address **without authentication or encryption**: anyone on that network can rewrite the virtual network. Restrict both ports to the controller and compute addresses with a firewall (not done or tested in the lab, which relied on a private lab network).

Gate: `ovs-appctl -t /var/run/ovn/ovnnb_db.ctl cluster/status OVN_Northbound` (and `ovnsb_db.ctl ... OVN_Southbound`) shows three servers and one leader; `ovn-nbctl --db=<list of three> show` answers from any node. The controllers register no chassis (Ubuntu's `ovn-central` does not install `ovn-controller`).

### 3.4 CloudX on the controllers

Install per service, not with `cloudx-controller`, which pulls all 30 services: `cloudx-common`, `-db`, `-cli`, `-auth-api`, `-compute-api`, `-conductor`, `-placement-api`, `-networking-api`, `-metadata-api`, `-image-api`, `-image-worker`, `-block-store-api`, `-key-manager-api`, `-ha-api`, `-telemetry-api`, `-dashboard`, `-reaper`, with `--no-install-recommends`. Apt adds only `nginx`. Install on `ctl01` first so the package generates the secrets, then copy `/etc/cloudx/cloudx.conf` to the others (mode 600 root:root) **before** installing there. In it, point the shared settings at the VIP:

```ini
rabbitmq_url = amqp://cloudx:<random>@172.20.0.151:5672/
database_url = postgresql+psycopg2://cloudx:<random>@172.20.0.151:5432/cloudx
auth_api_url = http://172.20.0.151:8005
ovn_nb_db = tcp:172.20.0.138:6641,tcp:172.20.0.145:6641,tcp:172.20.0.146:6641
ovn_sb_db = tcp:172.20.0.138:6642,tcp:172.20.0.145:6642,tcp:172.20.0.146:6642
```

**Three package-shipped files override `cloudx.conf` and still point at `127.0.0.1`**: `/etc/cloudx/conductor/conductor.conf` (`database_url`; it uses the `postgresql+psycopg://` driver prefix, keep it), `/etc/cloudx/key-manager-api/key-manager-api.conf` (`database_url`, the separate database `cloudx_key_manager`) and `/etc/cloudx/networking-api/networking-api.conf` (`ovn_nb_db`). Edit them on every controller. Skipping this makes `cloudx-manage db sync` fail for `key_manager`. Run `cloudx-manage db sync` once, on `ctl01`, then restart `cloudx-*`. Gate: `/health` of every API answers on each controller and `id -u cloudx` is the same everywhere.

### 3.5 HAProxy and keepalived

Services that have to be reached on `VIP:port` must not listen on every address, or HAProxy cannot bind that port. Three things do:

- nginx: the package writes `listen <port>;`. Rewrite each `/etc/nginx/conf.d/cloudx-*.conf` to `listen <this node's management IP>:<port>;` and **restart** nginx; `systemctl reload nginx` keeps the old wildcard sockets open. The snippets are dpkg conffiles, so a same-version reinstall kept the edit; on an upgrade keep your version (`--force-confold`) or repeat the edit.
- `cloudx-dashboard` (8080) and `cloudx-metadata-api` (8775) bind `0.0.0.0` inside `shared.launcher`, with no setting. The lab replaced their `ExecStart` with a drop-in (`/etc/systemd/system/cloudx-dashboard.service.d/ha-bind.conf`: an empty `ExecStart=` followed by `ExecStart=/usr/local/sbin/cloudx-bind-direct dashboard /opt/cloudx/services/dashboard 8080 1 <this node's management IP>`; the same for `metadata-api`, port 8775, 2 workers) and this wrapper, a copy of `shared.launcher.run_api` that takes the address:

```python
#!/opt/cloudx/venv/bin/python
import os
import sys
from pathlib import Path

from shared.config import load_service_config
from shared.launcher import gunicorn_argv, resolve_workers

name, app_dir, port, workers, address = sys.argv[1:6]
workers = resolve_workers(name, int(workers), load_service_config(name))
gunicorn = str(Path(sys.executable).parent / "gunicorn")
os.execv(gunicorn, [gunicorn, *gunicorn_argv(app_dir, f"{address}:{port}", workers, "direct")])
```

A drop-in survives package upgrades; the nginx edit does not necessarily (see above).
- RabbitMQ: the cluster-ctl `listeners.tcp.1` line above.

keepalived (`/etc/keepalived/keepalived.conf`, priorities 150/100/90, peers are the other controllers' management addresses). All three are `BACKUP` with `nopreempt`, so a recovered controller does not take the VIP back:

```
global_defs { enable_script_security; script_user root }
vrrp_script chk_haproxy { script "/usr/bin/systemctl is-active --quiet haproxy"; interval 2; fall 2; rise 2 }
vrrp_instance VI_1 {
    state BACKUP
    nopreempt
    interface eth0
    virtual_router_id 151
    priority 150
    advert_int 1
    unicast_src_ip 172.20.0.138
    unicast_peer { 172.20.0.145 172.20.0.146 }
    virtual_ipaddress { 172.20.0.151/16 dev eth0 }
    track_script { chk_haproxy }
}
```

Consider a drop-in with `Restart=on-failure` for keepalived (not tested): it ships without one, and after a `kill -9` the VIP stayed on the crashed node while another node took it too (section 9, B1b).

HAProxy: replace `/etc/haproxy/haproxy.cfg` with the file below, the data tier and its defaults, and put one `listen` per API in `/etc/haproxy/conf.d/` (`CONFIG="/etc/haproxy/haproxy.cfg"` and `EXTRAOPTS="-f /etc/haproxy/conf.d"` in `/etc/default/haproxy`). Ubuntu's stock file defaults to HTTP mode with 50 second timeouts, which would break idle database and AMQP connections, so keep the `defaults` block:

```
global
    log /dev/log local0
    maxconn 4096
    user haproxy
    group haproxy
    daemon

defaults
    log global
    mode tcp
    option tcplog
    timeout connect 5s
    timeout client 30m
    timeout server 30m
    timeout check 5s

listen postgres
    bind 172.20.0.151:5432
    acl allowed src 172.20.0.138 172.20.0.145 172.20.0.146 172.20.0.149 172.20.0.150 172.20.0.151
    tcp-request connection reject if !allowed
    option httpchk GET /primary
    http-check expect status 200
    default-server inter 3s fall 3 rise 2 on-marked-down shutdown-sessions
    server ctl01 192.168.29.1:5432 check port 8008
    server ctl02 192.168.29.2:5432 check port 8008
    server ctl03 192.168.29.3:5432 check port 8008
# listen rabbitmq: same shape on :5672 with `option tcp-check`, no httpchk
# --- /etc/haproxy/conf.d/api.cfg: one listen per API port (8001-8006, 8010, 8014, 8020, 8080, 8775):
listen api_8005_auth
    bind 172.20.0.151:8005
    mode http
    option httplog
    option forwardfor
    timeout client 1h
    timeout server 1h
    option httpchk GET /health
    http-check expect status 200
    default-server inter 3s fall 3 rise 2
    server ctl01 172.20.0.138:8005 check
    server ctl02 172.20.0.145:8005 check
    server ctl03 172.20.0.146:8005 check
```

The `acl` lines matter: Patroni and RabbitMQ see every client as a cluster-ctl address, so `pg_hba.conf` cannot tell clients apart, and the database user is a superuser. Without them anything on the management network could reach `VIP:5432`. Use random passwords for the `cloudx` database user and the RabbitMQ user, not `cloudx/cloudx`. The dashboard frontend uses `balance source` because its sessions live in the memcached of the controller that served the login.

Write every service URL into `/etc/cloudx/auth-api/auth-api.conf` as `http://172.20.0.151:<port>` (`compute_api_url`, `image_api_url`, `networking_api_url`, `block_store_api_url`, `placement_api_url`, `metadata_api_url`, `telemetry_api_url`, `key_manager_api_url`, `ha_api_url`), then `cloudx-manage catalog sync --dry-run` and `catalog sync` as in [deb-install.md](../deb-install.md) section 4.1. On any machine that uses the CLI set `CLOUDX_AUTH_URL=http://172.20.0.151:8005`; the CLI takes the other URLs from the catalog.

Gate: all eleven ports answer on the VIP, `patronictl list` is healthy, and stopping HAProxy on the VIP holder moves the VIP in a few seconds.

## 4. Compute nodes and gateways

On `comp01` and `comp02`, copy `cloudx.conf` first. **Write the node name before the packages are installed**, or the worker registers as `cloudx-compute-01` and leaves a hypervisor you cannot delete:

```bash
sudo install -d -m 750 /etc/cloudx/compute-worker
printf '[DEFAULT]\ncompute_host = comp01\nnode_name = comp01\n' | sudo tee /etc/cloudx/compute-worker/compute-worker.conf
sudo apt install --no-install-recommends -o Dpkg::Options::=--force-confold <the compute packages of quickstart.md>
sudo cloudx-setup-compute --controller-ip 172.20.0.138 --node-ip 192.168.25.1 --node-name comp01 --provider-interface ens19
```

`--node-ip` is the Geneve address (tunnel network). The script writes the `--controller-ip` you gave into two places that must not name a single controller, so fix both. First `auth_api_url` in the six worker configs (`compute-worker`, `metadata-proxy`, `telemetry-worker`, `lb-worker`, `vpn-worker`, `bgp-worker`): it overrides `cloudx.conf`, and left alone every catalog lookup and every `ha-api` heartbeat of the node goes through that one controller. Then `ovn-remote` (the list of three), and mark both nodes as gateways:

```bash
for w in compute-worker metadata-proxy telemetry-worker lb-worker vpn-worker bgp-worker; do
  sudo sed -i 's#^auth_api_url = .*#auth_api_url = http://172.20.0.151:8005#' /etc/cloudx/$w/$w.conf
done
sudo chown -R root:cloudx /etc/cloudx && sudo chmod 640 /etc/cloudx/cloudx.conf   # the directory you created for the node name is root-only until then
sudo systemctl restart 'cloudx-*'
```

```bash
sudo ovs-vsctl set open_vswitch . external_ids:ovn-remote='"tcp:172.20.0.138:6642,tcp:172.20.0.145:6642,tcp:172.20.0.146:6642"' external_ids:ovn-cms-options=enable-chassis-as-gw
sudo systemctl restart ovn-controller
sudo cloudx-setup-ceph-secret        # the libvirt secret for client.cloudx; without it the first RBD VM fails with "error connecting: Operation not supported"
```

Gate: `ovn-sbctl --db=<list of three> show` lists both chassis with their tunnel addresses, `cloudx hypervisor list` shows both nodes `active`, and `sudo sh -c 'grep -l 172.20.0.138 /etc/cloudx/*/*.conf'` on a compute node finds nothing.

## 5. Two kinds of volumes

On the compute nodes: `cloudx-storage` and `cloudx-block-store-worker`, then `sudo cloudx-setup-storage --host comp01 --portal-ip 192.168.28.1 --device /dev/sdb --controller-ip 172.20.0.151` (the portal is the node's address on the iSCSI network). On the controllers, in `/etc/cloudx/block-store-api/block-store-api.conf`, enable both drivers:

```ini
[DEFAULT]
storage_lvm_driver = true
storage_ceph_rbd_driver = true
ceph_pool = volumes
block_store_lvm_hosts = comp01,comp02
```

**Every block-store worker consumes its own queue and also the shared `block-store` queue**, and Ceph volumes carry no host, so their requests go to the shared queue. A worker without the Ceph driver that picks one up fails it: with the Ceph driver only on one controller, 4 of 6 `ceph-rbd` volumes ended in `error` because the LVM workers took them. The workaround that was tested: give the Ceph driver (and `ceph_conf`, `ceph_keyring`, `ceph_args`, `ceph_user`, as in [ceph.md](ceph.md) section 4) to **every** worker, including the LVM ones on the compute nodes. Then 20 of 20 Ceph volumes were created, shared between five workers. You may also run Ceph-only workers on the controllers (`storage_lvm_driver = false`, `block_store_host = ctlNN`) so Ceph volume requests are still served when both compute nodes are down.

```bash
cloudx volume-type create gp2 --backend lvm --disk-type sata_ssd --max-iops 3000 --max-throughput 125
cloudx volume-type create ceph-rbd --backend ceph --disk-type sata_hdd
```

`cloudx server create` has no flag for the root volume type; without one the platform picked `ceph-rbd`. The compute API takes `volume_type` (and `boot_volume`, `server_group`) in the request body. Attach data volumes with `cloudx volume attach VOL --server S`; in 1.0.0, `cloudx server volume attach` and `detach` always fail with HTTP 422; use `cloudx volume attach` and `cloudx volume detach`.

## 6. A network and a first VM

Create the provider network as in [quickstart.md](../quickstart.md) step 7 with a range on the provider segment, a tenant network and router, upload CirrOS and boot a VM. Check where the router's gateway landed: `ovn-nbctl --db=<list of three> list gateway_chassis`. In the lab `networking-api`, running on controllers that have no chassis, picked `comp01` and wrote **one** row. Floating IPs answered from the provider segment itself; nothing on the management network could reach them without a route to the provider range, and the Internet was not reachable through the lab's provider gateway.

Metadata (`169.254.169.254`) timed out from VMs on the compute nodes: it needs a host address on the provider network at the gateway node (`scripts/setup-metadata-route.sh`, described as single-node behaviour in [metadata-proxy.md](../metadata-proxy.md)). The guests still got their keys through the config drive.

## 7. What did not work the way you would expect

- **Ceph-backed VMs cannot be protected by `ha-api`.** `cloudx ha protect` answers HTTP 422 "server volumes could not be located" because a Ceph volume has no host. The refusal is conservative: `ha-api` refuses any volume whose location it cannot determine, and it fences only by revoking iSCSI ACLs; it has no way to fence an RBD client, so evacuating from a node that may still be running could leave two writers on one image. A VM whose root volume is LVM on another node is protected and was evacuated (section 9, B8).
- **A Ceph data volume also blocks `cloudx ha protect`** (same reason), and the protected VM that was evacuated in B8 was moved while its old qemu was still running on the failed node: only its iSCSI access was revoked ("no watchdog seen; old VMs may still run"). `ha-watchdog` would shut such VMs down on an isolated node; it was not tested.
- **An unconfigured `ha-watchdog` restarts in a loop** on the compute nodes. Disable it until `ha_api_url` and the peers are set (`docs/ha.md`).
- **Dashboard sessions are kept in the memcached of one controller.** If that controller dies, its users log in again; the VIP frontend uses `balance source` so a user stays on one controller.
- **Deleting a volume that has snapshots leaves orphans.** On 1.0.0 `cloudx volume delete` accepts a volume with snapshots, and deleting a Ceph snapshot never removed the RBD snapshot, so the image stays behind. Remove snapshots with `rbd snap rm`, or `rbd snap purge`, before `rbd rm`. Fixed in 1.0.1: the delete is refused (409) while snapshots exist, and deleting a Ceph snapshot runs `rbd snap rm`.
- **A second gateway row does not give a working floating-IP failover here.** With one `gateway_chassis` row, losing the gateway's uplink or its chassis means losing the floating IPs until it comes back. Adding a second row by hand (`ovn-nbctl lrp-set-gateway-chassis <port> comp01 20` and `comp02 10`; the reconciler left it alone) moved the OVN port binding to `comp02` within 14 seconds, yet the floating IP stayed unreachable until `ovn-controller` returned on `comp01`. The cause was not found (a suspect is the provider switch keeping the old MAC location).
- **Two of three controllers lost is an outage of the control plane**, as for any majority-based design (section 9, B5). Existing VMs and their floating IPs keep working.
- **Volumes requested while the control plane or RabbitMQ was down stayed in `creating` for good** (six of them in the lab); delete them by hand.

## 8. Day-two notes

- Cold start of the whole control plane: start etcd, Patroni, RabbitMQ and the OVN database servers on all three controllers (do not wait for each one: etcd and RabbitMQ wait for their peers), then `ovn-northd`, nginx, the `cloudx-*` units, HAProxy and keepalived. Use `systemctl list-unit-files 'cloudx-*.service'` to list the units: a glob such as `systemctl start 'cloudx-*'` only matches units that are already loaded and may start nothing.
- After the cluster-ctl network of a controller was cut and restored, Patroni rejoined it as a replica by itself and etcd, RabbitMQ and the OVN databases rejoined too. Every controller-side fault in section 9 recovered without a manual step once the fault was removed, apart from the OVN restart trap in section 3.3 and B1b, where keepalived had to be restarted by hand.
- The default quota is 10 instances per project.
- `cloudx-setup-ceph-secret` creates a different libvirt secret UUID on each compute node. That does not matter for evacuation, which rebuilds the domain on the target.

## 9. What was measured

Before B10 the compute nodes still reached the catalog through `ctl01` (see section 4), so B1-B9 do not show what happens to the compute nodes when `ctl01` is lost; B10 does. Probes ran on `comp01` against the VIP: a read (`cloudx server list`, every 2 s), a write (create and delete a security group), an RPC cycle (create a 1 GB Ceph volume, wait for `available`, delete it) and a network write (create and delete a tenant network), plus a ping to a floating IP from the provider segment. "Gap" is the longest time between two successful samples. The healthy baseline was about 2.2 s per read, 4 s per write cycle, 5.5 s per RPC cycle. Every fault had an automatic revert.

| Test | Fault | Result |
|---|---|---|
| B1 | stop HAProxy + keepalived on the VIP holder | VIP moved in under 6 s; read gap 9.4 s, write gap 10.9 s; floating IP no loss |
| B1b | `kill -9` of both | read 5.5 s, write 9.6 s; keepalived did not restart and the VIP was on two nodes for about 8 s |
| B2a | `kill -9` of the PostgreSQL postmaster on the Patroni leader | Patroni noticed after 9 s and restarted it locally, no failover; read gap 14.7 s, write gap 20.0 s |
| B2b | leader cut off from the cluster-ctl network | new leader after 29 s (ttl 30 s); read gap 35.6 s, write gap 37.3 s; the node rejoined as replica after the revert |
| B3 | one RabbitMQ node stopped | read and write unaffected; RPC gap 38.1 s; services reconnected by themselves, no unit restarted |
| B4 | NB leader of OVN stopped (with northd given the full member lists) | network writes uninterrupted, new NB leader in under 12 s |
| B5 | two of three controllers stopped | control plane down (writes failed for about 135 s, read partly); recovered about 54 s after the controllers came back, no manual step; floating IP and VMs unaffected |
| B6 | gateway uplink down / gateway `ovn-controller` stopped (twice, the second time with a second gateway row) | floating IP lost for the whole fault each time (42 s, 57 s and 35 s); see section 7 |
| B7 | one Ceph OSD daemon stopped (the monitor and manager on that host stayed up) | VM on RBD kept writing, longest pause between writes 3 s; `HEALTH_WARN`, then `HEALTH_OK` after 102 s |
| B8 | `compute-worker` and `libvirtd` stopped on a compute node | host `fenced` and the protected VM (LVM root on the other node) evacuated and running after 96 s; recovered by hand as in [ha.md](../ha.md) |
| B9 | all CloudX and data-tier services stopped on the three controllers, then started controller by controller (a service restart, not a node reboot; HAProxy and keepalived were still running on `ctl01`) | reads back 60 s and writes 68 s after the last start script returned; RPC took about 3 minutes more (volume creation ran at about 50 s each while a backlog drained) |
| B10 | the whole stack stopped on `ctl01` alone (Patroni leader and VIP holder), after fixing `auth_api_url` on the compute nodes | VIP to `ctl02`, leader to `ctl03`; read gap 19.5 s, write gap 26.2 s; both compute nodes stayed `up` in `ha-api` with no false fencing; RPC failed during the outage and stalled about 40 s again after `ctl01` came back (queue mirrors re-synchronising, not verified) |

Running every service on three controllers showed no duplicate work: ten VMs created in a burst were handled without duplicates (for the nine servers whose id the conductor logged, exactly one controller logged work), with no lock or duplicate-key errors in the journals of `conductor`, `reaper`, `compute-api`, `block-store-api`, `placement-api`, `networking-api` and `image-worker`. During the burst `server list` took 8-10 s per call.

## 10. Not tested

`ha-watchdog` (self-fencing) was disabled on the compute nodes. TLS between services, a load balancer outside the controllers, more than three controllers, more than two compute nodes, and upgrades of an HA cloud were not tried. The lab ran the controllers with all in-scope services at about 2.1-2.5 GB of 8 GB used when idle, with load peaking near 12 for a minute after a restart.
