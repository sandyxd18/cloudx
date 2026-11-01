# Installing with .deb packages

How to install CloudX on Ubuntu 24.04 (amd64) with `apt`, one package per service, without `scripts/install-systemd.sh`. This document covers building the packages, installing them by role, spreading services across nodes, operating them, and upgrading lab nodes that were installed from the tarball.

For a single-machine install, start with [quickstart.md](quickstart.md). This page is the reference.

Important: the upgrade runbook in section 7 was tested on all six lab nodes: infra01 (the auth node), infra02, infra03, and compute01 to compute03 (compute01 and compute03 with VMs left running); see "Test results" in section 7. The rollback ("If it fails") was tested on infra03. Try it on one node first.

## 1. What is in the packages

`make deb` produces 47 packages in `dist/deb/`. The package version is the content of `VERSION` plus `-1` (for example `0.8.6-1`).

| Package | Contents |
|---|---|
| `cloudx-common` | `/opt/cloudx/venv` (dependencies and the `shared` module), the `cloudx` user and group, `/etc/cloudx/cloudx.conf` (created on first install), `/var/lib/cloudx`, `/run/cloudx` (tmpfiles), `/usr/bin/cloudx-manage` |
| `cloudx-db` | Alembic migrations for `cloudx-manage db sync`; does not run on its own |
| `cloudx-<unit>` (41 packages) | service code in `/opt/cloudx/services/<dir>`, systemd unit in `/lib/systemd/system`, command `/usr/bin/cloudx-<unit>`, conf `/etc/cloudx/<unit>/<unit>.conf`; APIs behind nginx also ship `/etc/nginx/conf.d/cloudx-<unit>.conf` |
| `cloudx-controller`, `cloudx-compute`, `cloudx-storage` | meta packages: `Depends` on the service packages of the role, `Recommends` on `cloudx-cli` and distro packages (see below) |
| `cloudx-cli` | the `cloudx` command with its own venv in `/opt/cloudx/cli/venv`; can be installed on its own |

The two BIND units (`cloudx-named-auth`, `cloudx-named-resolver`) ship inside `cloudx-dns-worker` and have no package of their own.

### Roles

| Meta package | Services |
|---|---|
| `cloudx-controller` | `auth-api`, `billing-api`, `billing-worker`, `block-store-api`, `compute-api`, `conductor`, `container-api`, `dashboard`, `database-api`, `dns-api`, `event-api`, `event-worker`, `file-store-api`, `ha-api`, `image-api`, `image-worker`, `k8s-engine-api`, `k8s-engine-worker`, `key-manager-api`, `load-balancer-api`, `messaging-api`, `metadata-api`, `networking-api`, `object-store-api`, `orchestration-api`, `orchestration-worker`, `placement-api`, `reaper`, `reservation-api`, `telemetry-api`, plus `cloudx-db`. `cloudx-dns-worker` is only a `Recommends` (see section 8) |
| `cloudx-compute` | `bgp-worker`, `compute-worker`, `ha-watchdog`, `lb-worker`, `metadata-proxy`, `telemetry-worker`, `vpn-worker`, plus `cloudx-setup-compute` and `cloudx-setup-ceph-secret` in `/usr/sbin` |
| `cloudx-storage` | `block-store-worker`, plus `cloudx-setup-storage` in `/usr/sbin` |

`cloudx-file-store-worker` and `cloudx-container-worker` belong to no role: both are tied to one specific node and are installed on their own.

The meta packages also `Recommends` `cloudx-cli` and the distro packages CloudX does not configure: PostgreSQL, RabbitMQ, memcached and OVN Central on the controller; libvirt, QEMU, Open vSwitch, `ovn-host` and `qemu-utils` on compute. `apt install` installs `Recommends` by default. For `cloudx-controller` that would pull in `cloudx-dns-worker` and `bind9`, and the distro's own `named` would immediately fight for port 53 before the host is prepared (section 8). That is why section 4.1 installs the controller with `--no-install-recommends` and names its Recommends one by one, except `cloudx-dns-worker`. Drop PostgreSQL and RabbitMQ from that list when they run on another host.

### APIs and ports

External ports are the same as in a tarball install.

| Front | Services (port) |
|---|---|
| nginx (20) | `auth-api` 8005, `billing-api` 8011, `block-store-api` 8004, `compute-api` 8003, `container-api` 8018, `database-api` 8019, `dns-api` 8016, `event-api` 8012, `file-store-api` 8017, `ha-api` 8020, `image-api` 8002, `k8s-engine-api` 8015, `key-manager-api` 8014, `load-balancer-api` 8008, `messaging-api` 8022, `networking-api` 8001, `orchestration-api` 8013, `placement-api` 8006, `reservation-api` 8023, `telemetry-api` 8010 |
| direct TCP (4) | `object-store-api` 8007, `metadata-api` 8775, `metadata-proxy` 8776, `dashboard` 8080 |

APIs behind nginx listen on `unix:/run/cloudx/<unit>.sock`; nginx listens on their external port.

## 2. Building the packages

On Ubuntu 24.04 amd64 (the venv that gets built contains binary wheels and is tied to Python 3.12):

```bash
sudo apt install dpkg-dev fakeroot python3-venv
make deb
ls dist/deb | wc -l        # 47
```

Requirements:

- `dpkg-deb`, `fakeroot`, and `python3` 3.12 with the `venv` module.
- Network: `pip` fills `/opt/cloudx/venv` (inside `cloudx-common`) and the CLI venv at build time.
- Node.js with `npm` for the dashboard UI assets. `make deb` builds them itself (`npm ci` if `node_modules` is missing, then `npm run build`) into `services/dashboard/app/static/`. Without `npm` the build only runs if `services/dashboard/app/static/index.html` already exists; otherwise it stops with an error. A dashboard package without these assets installs fine but answers `503 Dashboard not built yet`.

`make deb` empties `dist/deb/` first. To inspect the result:

```bash
dpkg-deb --info dist/deb/cloudx-image-api_*_amd64.deb
dpkg-deb --contents dist/deb/cloudx-image-api_*_amd64.deb
```

`scripts/check-debs.sh` runs these checks (plus lintian at error level) on every package; CI runs it on each push.

### Pinned dependencies

`requirements.txt` only gives ranges (`fastapi>=0.115.0`) and leaves transitive packages free. Without pins, `make deb` on the same commit on a different day can pull other versions: `starlette` once moved from 1.9.0 to 1.10.0 in a single day, although the lab had never run the new version. That is why `pip` is called with `-c`:

| Venv | Range | Constraints |
|---|---|---|
| `/opt/cloudx/venv` (`cloudx-common`) | `requirements.txt` | `packaging/constraints.txt` |
| `/opt/cloudx/cli/venv` (`cloudx-cli`) | `cli/requirements.txt` | `packaging/cli-constraints.txt` |

Both files are the `pip freeze` of a tested venv, without `cloudx-shared` and `cloudx-cli`, which are installed from source. Adding a package to `requirements.txt` without adding its pin makes `tests/packaging/test_constraints.py` fail; so does a pin that falls outside its range.

Raising a version is a deliberate decision, not a side effect of a build:

```bash
python3 -m venv /tmp/v && /tmp/v/bin/pip install -r requirements.txt      # no -c: newest versions within the ranges
python -m pytest                                                            # the whole suite, then test in the lab
/tmp/v/bin/pip freeze | grep -v ' @ ' | sort -f                             # replace packaging/constraints.txt (keep the comment above it)
```

Do the same for the CLI venv with `cli/requirements.txt` and `packaging/cli-constraints.txt`. These constraints are for Python 3.12 on Linux amd64, the same as the build requirement above.

## 3. A local apt repository for the lab

An official apt archive and signing are not part of this. For a lab a flat repository is enough:

```bash
cd dist/deb
dpkg-scanpackages . | gzip -9c > Packages.gz
```

On the installing node (copy the `dist/deb` directory there, for example to `/srv/cloudx-repo`, or serve it over HTTP from the build machine with `python3 -m http.server`):

```bash
echo 'deb [trusted=yes] file:/srv/cloudx-repo ./' | sudo tee /etc/apt/sources.list.d/cloudx.list
# over HTTP:  deb [trusted=yes] http://<build-machine>:8000 ./
sudo apt update
```

`trusted=yes` skips signature checks; use it only on a lab network. `apt update` prints a few `Ign:` and `Err:` lines for this repository (`Release`, `InRelease`, and `Translation-en`: `File not found - .../en.gz`). That is normal for a flat repository made by `dpkg-scanpackages`: those files are optional and `Packages` is still read (`Get: ... Packages [... B]`). To silence them, add `Acquire::Languages "none";` to `/etc/apt/apt.conf.d/99cloudx`. Without a repository, `apt` can also install files directly and resolves dependencies among the files named together:

```bash
sudo apt install ./cloudx-common_*.deb ./cloudx-image-api_*.deb
```

The `nginx`, `init-system-helpers` and `python3` packages come from the Ubuntu repositories.

### Downloading a release

Each GitHub release from v1.0.0-rc1 on carries the 47 `.deb` files (about 60 MB), the tarball `cloudx-<version>.tar.gz` and `SHA256SUMS`. Releases up to v0.8.6 have only the tarball. `SHA256SUMS` names every file, so it doubles as the index to download from:

```bash
TAG=v1.0.3-rc2                                  # the release you want
BASE=https://github.com/sandyxd18/cloudx/releases/download/$TAG
mkdir -p ~/cloudx-debs && cd ~/cloudx-debs
curl -fsSLO $BASE/SHA256SUMS
awk '$2 ~ /\.deb$/ {print $2}' SHA256SUMS | xargs -P 4 -I{} curl -fsSLO $BASE/{}
sha256sum -c SHA256SUMS --ignore-missing        # every .deb must say OK
```

- `sha256sum -c` is given `--ignore-missing` because `SHA256SUMS` also lists the tarball, which this does not download. A line that is not `OK` means a damaged or incomplete download: fetch that file again.
- `releases/latest/download` points at the newest release that is not a pre-release, so `BASE=https://github.com/sandyxd18/cloudx/releases/latest/download` works for a stable install. A pre-release such as `v1.0.0-rc1` is never "latest"; name its tag.
- Pre-release file names read `1.0.0.rc1`, the package version is `1.0.0~rc1-1` (`dpkg-deb -f <file> Version`, `dpkg -l cloudx-common`). GitHub rewrites `~` in asset names, so the release workflow renames the files before it writes `SHA256SUMS`. A `~` sorts before the final release, so `1.0.0-1` upgrades `1.0.0~rc1-1` as a normal upgrade.
- Download all 47 files into one directory on every node, even for a node that needs only a few of them: `apt install ./file.deb` resolves dependencies among the files you name, and each package depends on `cloudx-common` of the same version.
- Install by role as in section 4. Name the packages you need rather than the whole glob (section 7, step 4); `quickstart.md` step 3 shows the filter for a controller.
- Every node of a cloud must run one version. When you upgrade, download the new release into a new directory and keep the old one until the upgrade is verified; it is what a rollback installs (section 7).

## 4. Installing by role

The packages enable and start all units when they are installed (subject to `policy-rc.d`). Until the database and config are ready, services fail to start and are retried every 5 seconds (`Restart=always`); that is normal until the `db sync` and restart steps below are done.

### 4.1 Controller

First-time order:

1. Install the packages.

   ```bash
   sudo apt install --no-install-recommends cloudx-controller \
        postgresql rabbitmq-server memcached ovn-central cloudx-cli
   ```

   The list after `cloudx-controller` is that meta package's `Recommends` without `cloudx-dns-worker`. DNS is optional and needs host preparation first, so it is installed separately in step 8.

2. Prepare the prerequisites the packages do not configure. Follow `../SETUP.md` section 3: 3.1 (PostgreSQL: listen on the management network, user `cloudx`, databases `cloudx`, `cloudx_orchestration`, `cloudx_events`, `cloudx_billing`, `cloudx_k8s_engine`, `cloudx_key_manager`), 3.2 (RabbitMQ user `cloudx`), and 3.3 (OVN Central listeners 6641 and 6642). Sections 5 (admin account) and 6 (first network) of SETUP.md apply after the install.

   Give the PostgreSQL and RabbitMQ users random passwords, as in SETUP.md 3.1 and 3.2, and keep both for step 3:

   ```bash
   DB_PW=$(openssl rand -hex 16); MQ_PW=$(openssl rand -hex 16)
   sudo -u postgres psql -c "CREATE USER cloudx WITH PASSWORD '$DB_PW' SUPERUSER"   # then CREATE DATABASE as in SETUP.md 3.1
   sudo rabbitmqctl add_user cloudx "$MQ_PW"                                         # then set_user_tags and set_permissions (SETUP.md 3.2)
   ```

3. Edit `/etc/cloudx/cloudx.conf`.

   Five secrets (`auth_secret_key`, `auth_admin_password`, `key_manager_master_key`, `metrics_token`, `internal_token`) are generated by the package on first install. Do not change them: changing `auth_secret_key` invalidates every token. Reinstalls and upgrades only fill secrets that are empty and never touch existing ones. The file has mode `640 root:cloudx`.

   Keys you need to fill in or check:

   | Key | Value |
   |---|---|
   | `database_url` | PostgreSQL: `postgresql+psycopg2://cloudx:<DB_PW>@127.0.0.1:5432/cloudx` with the password from step 2 (the package default uses the password `cloudx`) |
   | `rabbitmq_url` | RabbitMQ: `amqp://cloudx:<MQ_PW>@127.0.0.1:5672/` with the password from step 2 (the package default uses the password `cloudx`) |
   | `memcached_url` | default `127.0.0.1:11211` |
   | `auth_api_url` | an auth address other nodes can reach, for example `http://192.168.10.10:8005`, not `127.0.0.1` |

   Other keys in `/usr/share/cloudx/cloudx.conf.sample` are filled in for the features you use (`dns_*`, `file_store_host`, `container_host`, `storage_lvm_driver`, `lvm_vg_name`).

   Admin password: `sudo grep auth_admin_password /etc/cloudx/cloudx.conf`.

4. Create the database schema. Services refuse to start before this.

   ```bash
   sudo cloudx-manage db sync
   ```

   `cloudx-db-migrate` still exists as an alias. It is safe to run again.

5. Write the service URLs in `/etc/cloudx/auth-api/auth-api.conf`.

   Single-node install: skip this step and step 6. The defaults (`http://127.0.0.1:<port>`) are already correct.

   Several nodes: on its first start auth registers each service from the `<name>_api_url` key it reads, or `http://127.0.0.1:<port>` if there is none, and after that never changes an existing entry. The package's default `/etc/cloudx/cloudx.conf` holds only `auth_api_url` among the `*_api_url` keys (the other keys are commented examples), so `catalog sync` has no other URL to read unless you write them where auth reads them: `/etc/cloudx/auth-api/auth-api.conf` on the node that runs `auth-api`. Write an address other nodes can reach for every service that does not run on the auth node, and also for those that do run there if other nodes or the CLI need to reach them (`127.0.0.1` is only reachable from that node itself):

   ```ini
   [DEFAULT]
   image_api_url = http://infra02:8002
   compute_api_url = http://192.168.10.10:8003
   # optional: the URL given to users when it differs from the one used between services
   # image_api_public_url = http://cloud.example.com:8002
   ```

   The keys are the `url_key` column in `services/shared/services.py`: `compute_api_url`, `image_api_url`, `networking_api_url`, `block_store_api_url`, `placement_api_url`, `object_store_api_url`, `load_balancer_api_url`, `metadata_api_url`, `telemetry_api_url`, `billing_api_url`, `event_api_url`, `orchestration_api_url`, `key_manager_api_url`, `k8s_engine_api_url`, `dns_api_url`, `file_store_api_url`, `container_api_url`, `database_api_url`, `ha_api_url`, `messaging_api_url`, `reservation_api_url` (and `auth_api_url` from `cloudx.conf`). The public variants are `<name>_api_public_url`. When a value holds several comma-separated URLs (`ha_api_url`), only the first one is registered.

6. Fill the service catalog in auth. The command needs `cloudx-auth-api` and `cloudx-db` on the same node (`cloudx-db` tells `cloudx-manage` where the services directory is); without `cloudx-db` it stops with `cannot find the services directory`.

   ```bash
   sudo cloudx-manage catalog sync --dry-run   # only prints the changes
   sudo cloudx-manage catalog sync
   ```

   Read the `--dry-run` output first: `+` is a new service or endpoint, `~` is an already registered URL being changed (`<old> -> <new>`). After `db sync`, auth has usually already started and registered every service with `127.0.0.1`, so many `~` lines here are normal. Run without `--dry-run` when the list is right. Section 5 explains the full rules.

7. Restart the services and check.

   ```bash
   sudo systemctl restart 'cloudx-*'
   systemctl --no-pager status 'cloudx-*'
   curl -s http://127.0.0.1:8005/health
   cloudx auth login --username admin --auth-url http://192.168.10.10:8005
   ```

8. (Optional) DNS. Prepare the host as in section 8 ("DNS"), then install `cloudx-dns-worker`.

The provider network and floating IPs are created after that (`../SETUP.md` section 6).

### 4.2 Compute node

1. Copy `/etc/cloudx/cloudx.conf` from the controller to this node before installing, so the secrets are the same on every node. The packages do not overwrite an existing file. The `cloudx` group does not exist before the package is installed, so the file is first put in place as `600 root:root`; the `cloudx-common` postinst changes it to `640 root:cloudx`.

   ```bash
   # ~/cloudx.conf is a copy from the controller (copy it with mode 600 and delete it afterwards: it holds secrets)
   sudo install -d -m 750 /etc/cloudx
   sudo install -m 600 -o root -g root ~/cloudx.conf /etc/cloudx/cloudx.conf
   rm ~/cloudx.conf
   ```

   If the packages are already installed, replace the generated file with the copy from the controller in one command that sets the right mode at once, then restart the services:

   ```bash
   sudo install -m 640 -o root -g cloudx ~/cloudx.conf /etc/cloudx/cloudx.conf
   rm ~/cloudx.conf
   sudo systemctl restart 'cloudx-*'
   ```

2. Install the packages and run the host script.

   ```bash
   sudo apt install cloudx-compute
   sudo cloudx-setup-compute --controller-ip 192.168.10.10 --node-ip 192.168.10.11 \
        --node-name cloudx-compute-01 --provider-interface enp0s9
   ```

   Other accepted options: `--central-ip` (same as `--controller-ip`), `--encap-ip` (same as `--node-ip`), `--provider-if`. Values can also come from the variables `CONTROLLER_IP`, `NODE_IP`, `NODE_NAME`, `PROVIDER_IF`. The defaults are `192.168.10.10`, `192.168.10.11`, `cloudx-compute-01`, `enp0s9`. The node name must be unique among compute nodes.

   The package does not run this script because it needs IPs and an interface name. What it does:

   | Step | What it does |
   |---|---|
   | 1 | `apt-get install` the host packages: QEMU/KVM, libvirt, Open vSwitch, `ovn-host`, strongSwan, BIRD, HAProxy, cloud-image-utils, and the build tools for `libvirt-python`; disables the distro's `strongswan`, `strongswan-starter` and `bird`; turns off the `charon` and `swanctl` AppArmor profiles |
   | 2 | `systemctl enable --now libvirtd openvswitch-switch` |
   | 3 | configures OVN: `ovn-remote=tcp:<controller>:6642`, `ovn-encap-type=geneve`, `ovn-encap-ip`, `system-id=<node name>` (also written to `/etc/openvswitch/system-id.conf` so the name does not change when Open vSwitch restarts); restarts `ovn-controller`; then deletes the old chassis from the Southbound DB (`ovn-sbctl chassis-del`) if the node was already registered under another name, for example the random UUID from the `ovn-controller` that `ovn-central` brings along (without that, OVN rejects the new chassis because of the duplicate encap IP) |
   | 4 | creates the `br-ex` bridge and plugs the provider interface into it |
   | 5 | writes `/etc/cloudx/<svc>/<svc>.conf` for `compute-worker`, `metadata-proxy`, `telemetry-worker`, `lb-worker`, `vpn-worker` and `bgp-worker`, holding `compute_host`, `node_name`, `host_ip`, `rabbitmq_url`, `database_url` and an `auth_api_url` pointing at the controller; no other `*_api_url`. The passwords in the two URLs come from the `/etc/cloudx/cloudx.conf` copied from the controller (a `127.0.0.1` host is replaced by the controller IP); file mode `640 root:cloudx` |
   | 6 | if the package venv (`/opt/cloudx/venv`) exists, installs `libvirt-python` into it (compiled on the node, needs network); skipped if the binding is already there, and if the venv does not exist yet the script only prints the command to run later |

   The addresses of other services (placement, networking, compute, image and so on) are not written in those six files. Workers on this node get them from the catalog in auth, so fill the catalog with addresses reachable from the compute node (4.1 steps 5 and 6) before starting the workers. Entries that have not been synced hold the default `http://127.0.0.1:<port>`, which a compute node cannot reach: the worker gets a `ConnectionError` without a service name and the compute host does not show up in placement. Run `catalog sync` first. A `CatalogError` that names a service only appears when that service is missing from the catalog altogether.

   When the provider interface exists, the script plugs it into `br-ex` and writes `ovn-bridge-mappings=physnet1:br-ex` (the physnet name comes from `PROVIDER_PHYSNET`, default `physnet1`; other existing mappings are kept). `networking-api` cannot write this itself because it runs as the `cloudx` user, which may not write to the Open vSwitch socket. The script does not set `ovn-cms-options=enable-chassis-as-gw`: mark the gateway nodes (usually the infra nodes) with `sudo ovs-vsctl set open_vswitch . external_ids:ovn-cms-options=enable-chassis-as-gw`. Without a marked node, the router gateway is placed on the chassis where `networking-api` runs. If the node's `cloudx.conf` has no `rabbitmq_url` or `database_url`, the script warns and uses `cloudx:cloudx`. The script's closing message adapts: when the package venv exists it suggests `systemctl restart 'cloudx-*'` and not `install-systemd.sh`.

   The six files are package conffiles and the script overwrites them every time it runs, including earlier manual edits. Because their content differs from the package's default conf, `dpkg` asks what to do on an upgrade that ships a new conf. Install with `--force-confold` (section 6.4) to keep your files.

3. Make sure the libvirt binding is in the package venv. Without it `compute-worker` still starts but cannot create VMs (`tarball-install.md` 4.1). `cloudx-setup-compute` installs it itself (step 6 in the table above) as long as the package is already installed when the script runs, as in the order above. Check:

   ```bash
   /opt/cloudx/venv/bin/python -c "import libvirt"
   ```

   If that fails (for example the script ran before the package was installed), install it yourself; step 1 already installed the build tools:

   ```bash
   sudo /opt/cloudx/venv/bin/pip install libvirt-python
   ```

   `dpkg` does not track that file, so it is not removed when `cloudx-common` is upgraded; install it again if the venv is rebuilt.

4. Restart the compute services.

   ```bash
   sudo systemctl restart cloudx-compute-worker cloudx-metadata-proxy cloudx-telemetry-worker \
        cloudx-lb-worker cloudx-vpn-worker cloudx-bgp-worker cloudx-ha-watchdog
   ```

   `ha-watchdog` settings are in `ha.md`. The node that runs it must have `ha_api_url` in its own conf, `/etc/cloudx/ha-watchdog/ha-watchdog.conf` (`[DEFAULT]`), not only in the catalog. With several `ha-api` instances, write them comma-separated: `ha_api_url = http://a:8020,http://b:8020`. There are two reasons. An override beats the catalog, so the watchdog's fencing decision does not depend on a catalog lookup. And the catalog keeps only the first URL of a comma-separated list, so failover to a second instance only works through this override.

   Without an override, the watchdog saves the last ha-api URL that resolved to `/var/lib/cloudx/ha-watchdog/ha_api_url` (it survives restarts and reboots) and uses it when the catalog is unreachable, so a dead auth is not mistaken for a silent ha-api; fencing still only happens when ha-api really does not answer. One case still counts as "ha-api silent": a watchdog that starts cold while auth is unreachable and has never saved a URL (a new node, for example). In that case only the override above protects you.

5. Nodes with Ceph RBD disks: once `/etc/ceph/ceph.conf` and the user's keyring (default `client.cloudx`) exist on this node, run:

   ```bash
   sudo cloudx-setup-ceph-secret                      # user cloudx, keyring /etc/ceph/ceph.client.cloudx.keyring
   sudo cloudx-setup-ceph-secret --user cloudx --keyring /etc/ceph/ceph.client.cloudx.keyring
   sudo systemctl restart cloudx-compute-worker
   ```

   The script defines the libvirt secret from the keyring (reusing an existing UUID), then writes `ceph_user` and `ceph_secret_uuid` to `/etc/cloudx/compute-worker/compute-worker.conf`. It is safe to run again. The Ceph client and keyring are not installed by the packages.

### 4.3 Storage node

```bash
# cloudx.conf copied from the controller as in 4.2
sudo apt install cloudx-storage
```

`block-store-worker` needs per-node settings in `/etc/cloudx/block-store-worker/block-store-worker.conf`: `block_store_host`, `iscsi_export`, `iscsi_portal_ip`, `storage_lvm_driver`, `lvm_pv_name`, `lvm_vg_name`, plus `rabbitmq_url` and `database_url` pointing at the controller. These per-node keys are written by `sudo cloudx-setup-storage --host <name> --portal-ip <ip> [--device /dev/sdb] [--controller-ip <ip>]`, the script shipped in the `cloudx-storage` package (in a repo checkout it is `scripts/prepare-lvm-node.sh`; the full steps are in `two-node-lvm-lab.md`). The script overwrites that conf file (mode `640 root:cloudx`). With `--controller-ip` it also writes `rabbitmq_url` and `database_url`, with the passwords from the controller's `/etc/cloudx/cloudx.conf` copy and the controller IP as host; without that option (for example on an all-in-one node with `--local-api`) the two keys are not written and the values from `cloudx.conf` apply.

For a single machine that also runs `cloudx-block-store-api` (an all-in-one install), add `--local-api`: the script adds the host name to `block_store_lvm_hosts` in `/etc/cloudx/block-store-api/block-store-api.conf` (replacing the commented example line, or appending to the existing list without duplicates). On multi-node installs, set `block_store_lvm_hosts` on the controller to all storage nodes. Afterwards restart `cloudx-block-store-worker` and `cloudx-block-store-api`, then create a volume type (`cloudx volume-type create gp2 --backend lvm ...`): every server creates its root volume in block-store, so without a volume type `server create` is rejected with "No volume types are configured".

### 4.4 CLI only

```bash
sudo apt install cloudx-cli
cloudx auth login --username admin --auth-url http://192.168.10.10:8005
```

## 5. Spreading services across nodes

A single API package can be installed on any node, not only on the controller. Installing on another node needs:

- The same `/etc/cloudx/cloudx.conf` as the controller (identical secrets), with `auth_api_url` pointing at auth.
- `database_url` and `rabbitmq_url` pointing at PostgreSQL and RabbitMQ (in that node's conf or in the unit's conf).
- `cloudx-manage db sync` does not need to run there; the schema is created once from any node that has `cloudx-db` and access to the database.

Services find each other through the service catalog in auth. The URL lookup order: a `<name>_api_url` key in the conf or environment if present (an override), then the catalog from `GET /internal/catalog` (cached for `catalog_cache_ttl`, default 60 seconds), then a `CatalogError` that names the service. Only `auth_api_url` must exist on every node.

Each service's URL is written in one place only, the place the catalog is filled from: `/etc/cloudx/auth-api/auth-api.conf` on the node that runs `auth-api`. Do not put them in `cloudx.conf`: keys there are read by every service as overrides and beat the catalog. For a single-node install the `127.0.0.1` default is correct and nothing needs to be written.

To install or move an API to another node:

1. Install the API package on the target node (with the same `cloudx.conf`, see above).
2. On the auth node, write that service's URL in `/etc/cloudx/auth-api/auth-api.conf`, with an address reachable from other nodes:

   ```ini
   [DEFAULT]
   image_api_url = http://192.168.10.20:8002
   ```

3. Run `sudo cloudx-manage catalog sync --dry-run` on the auth node, read the `+` and `~` lines, then run `sudo cloudx-manage catalog sync` without `--dry-run`.
4. Stop and remove the API package on the old node if there was one.

The list of keys is in 4.1 step 5.

`catalog sync` only adds services and updates endpoints whose URL differs, prints every change, and never deletes. Missing or empty keys are left alone. When a value holds several comma-separated URLs (for example `ha_api_url`), only the first one is registered. A `<name>_api_public_url` key (for example `image_api_public_url`) fills the `public` endpoint when it differs from `internal`. A normal start only registers services that have no endpoint yet and does not change existing ones; only `catalog sync` changes them. So an admin's edits through the API are not silently overwritten, and a new URL in `auth-api.conf` does not take effect until `catalog sync` is run. Other services pick up the change within one cache period, without a restart. The catalog is fetched at most once per `catalog_cache_ttl` (60 seconds). When a fetch fails (auth down or slow), the client does not retry for 5 seconds (`CATALOG_RETRY_BACKOFF`, `0` turns it off) and uses the old cache during that time, or returns `CatalogError` at once if there is no cache yet, without waiting for a timeout on every request.

The CLI uses the `public` endpoint from the catalog in the login response. `CLOUDX_<SVC>_URL` still applies as an override.

### What is tied to a node

| Component | Tie |
|---|---|
| `block-store-worker` | the node where the volumes live (node name in `block_store_host`, node list in `BLOCK_STORE_LVM_HOSTS` on the controller, see `two-node-lvm-lab.md`) |
| `file-store-worker` | the node whose name equals `file_store_host`; needs NFS-Ganesha |
| `container-worker` | every node that sets `container_host`; needs Docker |
| `dns-worker` and BIND | one node with two IPs: `dns_public_address` (auth) and `dns_resolver_address` (resolver) |
| `compute-worker` and the other compute workers | one per compute node, unique node name |
| OVN Central, PostgreSQL, RabbitMQ | a single instance, not managed by the packages; their HA is out of scope |

`ha-api` may run on two nodes (see `ha.md`).

## 6. Operations

### 6.1 Number of API workers

The `workers` key in `/etc/cloudx/<unit>/<unit>.conf` sets the number of gunicorn workers. Priority: the unit conf, then `cloudx.conf`, then the default baked into `/usr/bin/cloudx-<unit>` at build time (from `packaging/manifest.toml`). `/usr/share/cloudx/manifest.toml` is only a copy for reading; editing it changes nothing.

```ini
[DEFAULT]
workers = 4
```

```bash
sudo systemctl restart cloudx-compute-api
```

A value that is not a positive integer (`0`, `-1`, `abc`, empty) makes the unit stop with a message naming the unit and the key, and it is retried every 5 seconds. This key only applies to API units.

### 6.2 Status and logs

```bash
systemctl --no-pager status 'cloudx-*'
systemctl is-active cloudx-image-api
journalctl -u cloudx-image-api -f
sudo nginx -t
```

`journalctl` works per unit as in the tarball install. nginx logs are in `/var/log/nginx/`.

### 6.3 Removing an API package

`apt remove cloudx-image-api` (for example) stops the unit, but the conffiles stay, including the nginx snippet. The external port then answers `502` from nginx because the socket is gone, until the package is installed again or the snippet is deleted. `apt remove` does not reload nginx. `apt purge` deletes that snippet and the unit's conf (including your edits) and `postrm` reloads nginx itself when `nginx -t` passes, so the route disappears from nginx. If `nginx -t` fails, `postrm` only prints a warning; fix the config and run:

```bash
sudo systemctl reload nginx
```

### 6.4 Conffiles and upgrades

| File | Treatment |
|---|---|
| `/etc/cloudx/<unit>/<unit>.conf` | conffile of the unit's package |
| `/etc/nginx/conf.d/cloudx-<unit>.conf` | conffile of the packages fronted by nginx |
| `/etc/cloudx/cloudx.conf` | not a conffile; created by `cloudx-common` when missing and never overwritten |

`dpkg` only asks when the new package ships different conffile content and the file on disk has been changed by you. To install without questions and keep your files:

```bash
sudo apt install -o Dpkg::Options::=--force-confold cloudx-image-api
sudo apt upgrade -o Dpkg::Options::=--force-confold
```

Version upgrades: the package restarts its own units. If the new version brings migrations, services refuse to start until `sudo cloudx-manage db sync` is run on the controller; after that `sudo systemctl restart 'cloudx-*'`.

Installing a new API reloads nginx automatically; if `nginx -t` fails, the package is still installed and the error is printed. Fix the config, then `sudo systemctl reload nginx`.

### 6.5 Rotating the database and RabbitMQ passwords

The passwords live in three places: the `cloudx` role in PostgreSQL and the `cloudx` user in RabbitMQ (controller), `/etc/cloudx/cloudx.conf` on every node, and the per-unit confs that have their own `database_url` or `rabbitmq_url`: the confs written by `cloudx-setup-compute` and `cloudx-setup-storage`, plus `conductor`, `event-api`, `billing-api`, `orchestration-api`, `key-manager-api` and `k8s-engine-api`, which the packages ship with the password `cloudx` and their own database name. Per-unit confs override `cloudx.conf`, so those units must be changed too.

The order: change all confs first (the old password still works, so nothing breaks), then change the servers, then restart. Connections that are already open are not cut by a password change; the control plane is only disturbed during the restart (about a minute), and running VMs are not affected.

1. Back up the confs on every node. The archive holds secrets; delete it once everything runs.

   ```bash
   sudo sh -c 'umask 077; tar czf /root/cloudx-etc-before-rotate.tgz -C /etc cloudx'
   ```

2. Generate the two passwords once (on the controller), then run this on **every node**, with the same `DB_PW` and `MQ_PW`. The command changes only the `database_url` and `rabbitmq_url` lines for the `cloudx` user and keeps file modes.

   ```bash
   DB_PW=$(openssl rand -hex 16); MQ_PW=$(openssl rand -hex 16)
   sudo grep -rIl '://cloudx:' /etc/cloudx | sudo xargs sed -i -E \
     -e "s#^(database_url *= *[^ :]+://cloudx:)[^@ ]*@#\1${DB_PW}@#" \
     -e "s#^(rabbitmq_url *= *[^ :]+://cloudx:)[^@ ]*@#\1${MQ_PW}@#"
   ```

3. On the controller, change the passwords on the servers. The PostgreSQL password goes through stdin so it does not show in the process list; `rabbitmqctl change_password` only accepts arguments, so that password is briefly visible to local users of that host.

   ```bash
   echo "ALTER USER cloudx PASSWORD '${DB_PW}'" | sudo -u postgres psql
   sudo rabbitmqctl change_password cloudx "${MQ_PW}"
   ```

4. On every node, restart only the active units. `systemctl restart 'cloudx-*'` would also start optional units that were disabled on purpose.

   ```bash
   sudo systemctl restart $(systemctl list-units 'cloudx-*' --state=active --no-legend | awk '{print $1}')
   ```

5. Check: no `cloudx:cloudx@` left, no authentication errors, and `cloudx hypervisor list` and VM creation work.

   ```bash
   sudo grep -rIl 'cloudx:cloudx@' /etc/cloudx                      # empty
   sudo journalctl -u 'cloudx-*' --since -3min | grep -i -E 'password authentication failed|ACCESS_REFUSED'   # empty
   ```

If it fails before step 3, restore the confs from the backup (`sudo tar xzf /root/cloudx-etc-before-rotate.tgz -C /etc`). After step 3 the old passwords no longer work, so restore the confs from the backup **and** put the old passwords back on the servers.

## 7. Upgrading lab nodes from the tarball

The order and commands below are put together from the package contents and from `install-systemd.sh`; run them on one low-risk node first (not the auth node), check, then continue.

**Test results (2026-10-22).** Steps 1 to 5 and 7 were run on `infra03` of the 6-node lab (Ubuntu 24.04, 10 services: container-api, database-api, file-store-api, ha-api, k8s-engine-api and its worker, messaging-api, orchestration-api and its worker, reservation-api; that node also holds one Ceph mon and OSD). The results:

- The ten active units were the same as before the upgrade and none was `failed`; 8 APIs answered `200` on `127.0.0.1` and from another node through nginx; `nginx -t` passed; `cloudx.conf` stayed `640 root:cloudx` with all five secrets intact.
- Authenticated requests from the CLI on another node (`container list`, `database list`, `stack list`, `cluster list`, `queue list`, `reservation list`, `ha hosts`) succeeded.
- Two errors were found and fixed in the document and code: steps 4 and 7 and the rollback read `/root/cloudx-active-before.txt` without `sudo` (`/root` has mode `700`, so step 4 stopped before `apt install`), and gunicorn logged `Control server error: Permission denied: '/opt/cloudx/.gunicorn'` for every API because the `cloudx` user's home (`/opt/cloudx`) is owned by root; the launcher now passes `--no-control-socket`.
- Right after installation, workers logged `Max retries exceeded` to their own API until that API started (units start in sequence); it went away within seconds.

**Follow-up results (2026-10-22).**

- `infra03` was reinstalled with the fixed `cloudx-common`: no more control socket errors after a restart.
- `compute02` (no VMs; `bgp-worker`, `compute-worker`, `lb-worker`, `metadata-api`, `metadata-proxy`, `telemetry-worker`, `vpn-worker`): steps 1 to 6 and `libvirt-python` worked. The node registered in Placement and in the hypervisor list. A cirros VM created through the API landed on compute02, booted to the login prompt and got DHCP from OVN; the VM and its root volume were then deleted.
- `infra01` (the auth node, also PostgreSQL, RabbitMQ and OVN central; `auth-api`, `block-store-worker`, `compute-api`, `conductor`, `dashboard`, `load-balancer-api`, `metadata-api`, `networking-api`, `placement-api`, `reaper`, plus `cloudx-db` and `cloudx-cli`): the whole runbook, including `db sync` (no changes) and `catalog sync`, ran in one script with a `pg_dumpall` as backup. Ten active units were the same as before and both existing VMs stayed `ACTIVE`. A CLI with a clean environment (not a single `CLOUDX_<SVC>_URL`) found the services on the three infra nodes through the catalog alone. The dashboard WebSocket (`/ws/events`) answered `101 Switching Protocols` under gunicorn.
- Three more errors were found in this test and fixed: the dashboard package was built without UI assets and answered `503` (`make deb` now builds the assets or stops with an error); `cloudx` from `cloudx-cli` failed with `No module named 'tabulate'` because `cli/requirements.txt` did not list it; and `cloudx-manage catalog sync` needs `cloudx-db` on the same node (now written in 4.1 step 6).

- `infra02` (`image-api` and worker, `block-store-api`, `object-store-api`, `key-manager-api`, `telemetry-api`, `billing-api` and worker, `event-api` and worker): steps 1 to 5 and 7 ran in one script with a pre-check before anything was stopped. Ten active units were the same as before, every API answered `200` locally and from another node, and `/var/lib/cloudx/images` was intact. A CLI with a clean environment reached image, volume, secret, alarm, event and object-store. A 20 MB upload through nginx to `image-api` (far above nginx's default 1 MB limit) finished in 1.4 seconds and the checksum of the file on disk matched the source.

- The rollback ("If it fails") was tested on `infra03` after that node went to packages: `apt remove`, removing the snippets, restoring `/opt/cloudx` and the 43 old units, then `enable --now` for 10 units. The result was 10 active units, the same as before the upgrade, processes running from `/opt/cloudx/venv/bin/gunicorn` with `--bind 0.0.0.0:<port>` (tarball code; `launcher.py` and `/usr/bin/cloudx-*` gone), every API answering `200`, `cloudx.conf` intact, and Ceph on that node still `HEALTH_OK`.
- Reinstalling after the rollback at first failed silently: the packages were installed but the units were `disabled` and the nginx snippets were missing (two dpkg behaviours, see "Installing the packages again after a rollback"). The node was recovered with the commands now written there (`--force-confmiss` and `systemctl enable --now`): 10 units active and `enabled`, 8 nginx snippets back, every API `200` locally and from another node. This fix was tested as a recovery on a node that had already been installed without active units, not as a second full rollback cycle.

- `compute01` and `compute03` (each with one running VM, root disk on Ceph RBD; same units as compute02): the upgrade was done without stopping or moving the VMs. The script recorded the libvirt domains, qemu PIDs and CPU time before anything, while the CloudX units were stopped, and after the upgrade. The VMs stayed `running` with the same qemu PID before and after (`15124` on compute01, `17357` on compute03) and CPU time kept rising (the VMs were idle at the login prompt, so only slightly); the qemu processes sit in `machine.slice` under `systemd`, not in a `cloudx-*` unit, so stopping CloudX units does not touch them. `seed.iso` in `/var/lib/cloudx/compute/instances/` was intact, `ceph_user` and `ceph_secret_uuid` in `compute-worker.conf` were kept (conffile kept by `--force-confold`), the libvirt secret for Ceph did not change, and `libvirt-python` was installed in the package venv. After the upgrade the worker listened on the `compute.<node>` queue and registered in Placement; `server pause` then `server resume` through the API moved libvirt (`paused`, then `running`) with the same qemu PID and status `ACTIVE` in the API.

The whole lab cluster (infra01, infra02, infra03, compute01, compute02, compute03) now runs from packages: no tarball units are left, no unit has failed, `catalog sync --dry-run` reports `catalog already up to date`, and both RBD VMs stayed `ACTIVE`.

**Result with the release assets (2026-10-27).** One machine with everything on it (controller, compute and LVM storage; 0.8.6 tarball code from the `v0.8.6` tag installed with `install-systemd.sh` and `setup-compute-node.sh`, volume group on the loop file), running a CirrOS VM with a floating IP and an attached volume. Upgraded to the `1.0.0~rc1-1` packages downloaded from the `v1.0.0-rc1` release page, steps 1 to 7. Afterwards: 30 of the 31 earlier units active (the 31st is `cloudx-storage-init`, see step 2), none failed; every API that is installed on the node answered `200`; no file under `/etc/cloudx` changed or disappeared (the packages added their own files and 6 `.dpkg-dist` files next to conffiles that were already there); `db sync` applied the two new migrations (`messaging`, `reservation`) and `catalog sync --dry-run` said `catalog already up to date`; the VM kept its QEMU process and answered ping and SSH on the floating IP throughout. Three things went wrong and are now in the steps above:

- Step 4 as it was written (`apt install cloudx-common $PKGS`) cannot work without an apt repository, and a release gives you only files. It now installs from the downloaded files.
- Step 2 deleted `cloudx-storage-init.service`, which has no package. A reboot after the upgrade left no volume group (`vgs` empty), and the VM was down with its volumes missing. Putting the unit back (from `/root/cloudx-units.tgz`) and enabling it brought the volume group, both logical volumes and the VM back. Step 2 now keeps the unit.
- The node had been set up by the 0.8.6 script, so after the reboot its chassis came back under a random UUID and the floating IP stopped answering. The repair in `troubleshooting.md` fixed it; step 6 now says to check this before the first reboot.

A clean install with no earlier tarball was tested later on a single VM; see [quickstart.md](quickstart.md). What the compute tests above do not prove: a new VM scheduled to compute01 or compute03 after the upgrade (the test VM on compute02 was created before those two nodes were upgraded; the scheduler puts new VMs on the least loaded node), and a full restart of a node with running VMs.

Why a plain `apt install` does not work:

- Old units in `/etc/systemd/system/cloudx-*.service` override the package units in `/lib/systemd/system`, so the old units must be removed.
- `dpkg` refuses to overwrite files it does not own. The tarball's `/opt/cloudx` content (venv, `services`, `cli`) collides with the package content, so it has to be moved out of the way.
- nginx must be able to use the ports gunicorn used to hold, so the old units must stop first.
- `/etc/cloudx` and `/var/lib/cloudx` are kept.

Order between nodes: nodes that only run a few services first, the node with auth and PostgreSQL last. While a node is being worked on, its services are down (a few minutes). After the move to packages, URLs between nodes do not change because the external ports are the same. `*_api_url` keys still in the old conf now act as overrides that beat the catalog, so services on that node keep using the old address until the key is removed; check the catalog with `catalog sync --dry-run` (step 8) before removing them.

### Per node

1. Check and record the starting state.

   ```bash
   # five secrets must be filled in (result 5), and their values must be the same on every node
   sudo grep -cE '^(auth_secret_key|auth_admin_password|key_manager_master_key|metrics_token|internal_token) *= *[^ ]' /etc/cloudx/cloudx.conf

   systemctl list-units 'cloudx-*.service' --state=active --no-legend | awk '{print $1}' | sudo tee /root/cloudx-active-before.txt
   sudo tar czf /root/cloudx-etc.tgz /etc/cloudx                    # holds secrets, keep it somewhere safe
   sudo tar czf /root/cloudx-units.tgz /etc/systemd/system/cloudx-*
   ls -d /etc/systemd/system/cloudx-*.service.d 2>/dev/null
   ```

   `/root` has mode `700`, so the files there can only be read with `sudo`. That is why steps 4 and 7 and the rollback use `sudo cat`.

   `*.service.d` directories (drop-ins, for example `provider-network.conf` from `tarball-install.md` 2.7) still apply after the old unit is deleted and can override the package unit. Move their content into the unit's conf (`Environment=PROVIDER_CIDR=...` becomes `provider_cidr = ...`) and delete the drop-in.

2. Stop and disable all old units, then delete them.

   ```bash
   systemctl list-unit-files 'cloudx-*.service' --state=enabled --no-legend | awk '{print $1}' | grep -v '^cloudx-storage-init' | xargs -r sudo systemctl disable --now
   sudo systemctl stop 'cloudx-*'
   ls /etc/systemd/system/cloudx-*.service | grep -v storage-init | xargs -r sudo rm
   sudo systemctl daemon-reload
   ```

   `systemctl disable` does not accept the `cloudx-*` pattern, so the unit list is fetched first.

   Keep `cloudx-storage-init.service` if the node has it. `install-systemd.sh` creates it together with the volume group `vg-cloudx` on the loop file `/var/lib/vg-cloudx.img`, and it is what attaches that file again at boot. No package replaces it. Without it the volume group is gone after the next reboot and the volumes of running VMs are missing (`vgs` prints nothing), although `cloudx server list` still says `ACTIVE`. A node whose volume group is on a real disk has no such unit.

3. Move the tarball content and its old links out of the way.

   ```bash
   sudo mv /opt/cloudx /opt/cloudx.tarball
   [ -L /usr/local/bin/cloudx ] && sudo rm /usr/local/bin/cloudx   # link to the old venv, shadows /usr/bin/cloudx
   ```

4. Install the package for every service that ran on this node.

   ```bash
   cd ~/cloudx-debs          # the directory of "Downloading a release" (section 3)
   PKGS=$(sudo cat /root/cloudx-active-before.txt | sed -e 's/\.service$//' -e '/^cloudx-named-/d' -e '/^cloudx-storage-init$/d')
   sudo apt install -o Dpkg::Options::=--force-confold ./cloudx-common_*.deb $(for p in $PKGS; do echo ./${p}_*.deb; done)
   ```

   On the node that used to run `make migrate`, add `./cloudx-db_*.deb`; on nodes that use the CLI, add `./cloudx-cli_*.deb`. The BIND units ship inside `cloudx-dns-worker`. Do not use `./cloudx-*.deb`: that installs all 47 packages, including every meta package. With a local apt repository (section 3) the package names alone work: `sudo apt install -o Dpkg::Options::=--force-confold cloudx-common $PKGS`.

   `--force-confold` keeps the existing `/etc/cloudx/<unit>/<unit>.conf`. The old `cloudx.conf` is used as it is, including the `*_api_url` keys in it (overrides); the package only fills empty secrets and resets its mode to `root:cloudx` `640`.

5. Make sure nginx loads the new snippets.

   ```bash
   sudo nginx -t
   sudo systemctl restart nginx
   ```

6. Compute nodes: install `libvirt-python` again. The tarball venv had it, the package venv does not. Without it `compute-worker` falls back to `virsh` and `telemetry-worker` cannot import libvirt, while systemd still reports the units `active`.

   ```bash
   sudo /opt/cloudx/venv/bin/pip install libvirt-python
   sudo systemctl restart 'cloudx-*'
   ```

   The build tools it needs (`libvirt-dev`, `pkg-config`, `build-essential`, `python3-dev`) are already there from the `setup-compute-node.sh` that was run earlier; if not, see 4.2 steps 2 and 3.

   A compute node that was set up by 0.8.x (or older) `setup-compute-node.sh` also needs its OVN name made permanent, or it loses it at the next Open vSwitch start, which a reboot is enough to cause. Run this check before the first reboot (the problem and its repair are in `troubleshooting.md`, "Chassis renamed after Open vSwitch restarted"):

   ```bash
   cat /etc/openvswitch/system-id.conf
   sudo ovs-vsctl get open_vswitch . external_ids:system-id
   ```

   If the two differ, the second one is the name the node has now: `echo <that name> | sudo tee /etc/openvswitch/system-id.conf` changes nothing that is running. Left as it is, the chassis comes back under the file's random UUID after a reboot, the router gateways point at a chassis that no longer exists, and floating IPs stop answering until the repair in `troubleshooting.md` is done.

7. Verify.

   ```bash
   # the active units match the earlier ones (only cloudx-named-* units may differ)
   diff <(sudo cat /root/cloudx-active-before.txt | sort) <(systemctl list-units 'cloudx-*.service' --state=active --no-legend | awk '{print $1}' | sort)

   # every API installed on this node; ports that are not installed answer 000
   for p in 8001 8002 8003 8004 8005 8006 8008 8010 8011 8012 8013 8014 8015 8016 8017 8018 8019 8020 8022 8023; do
       printf '%s ' "$p"; curl -s -o /dev/null -w '%{http_code}\n' --max-time 5 "http://127.0.0.1:$p/health"
   done
   ```

   Every API on this node must answer `200`. Repeat the `curl` from another node with `http://<node>:<port>/health` to confirm nginx answers on the external address. The four direct services (8007, 8775, 8776, 8080) are checked with `systemctl is-active`.

   On the node with auth:

   ```bash
   sudo cloudx-manage db sync                  # changes nothing when the schema is current
   sudo cloudx-manage catalog sync --dry-run   # only prints: "catalog already up to date" or a list of changes
   ```

   The result of this `--dry-run` is reviewed in step 8.

   On compute nodes, `import libvirt` must succeed (an `active` status alone does not prove it):

   ```bash
   /opt/cloudx/venv/bin/python -c "import libvirt"
   ```

   From a node that has the CLI: `cloudx server list` must still work (see step 8 if it fails for some services).

8. Align the catalog on the auth node, once all services above are active. On a tarball lab the catalog holds what auth filled in at its first start, and a normal start never corrects it. For services whose `*_api_url` was never written, the entry can be a docker-style name that cannot be reached (for example `http://compute-api:8000`, `http://telemetry-api:8010`, `http://metadata-api:8775`). After the upgrade the CLI follows the `public` endpoint from the catalog, no longer `127.0.0.1:<port>`, so an entry like that makes the CLI on that node fail for the service concerned (and so do other services without an override).

   ```bash
   sudo cloudx-manage catalog sync --dry-run
   ```

   If there are `~` lines that replace a docker-style name, or other URLs that other nodes and the CLI cannot reach, write the right URL in `/etc/cloudx/auth-api/auth-api.conf` (key `<name>_api_url`, same form as 4.1 step 5; keys already in that node's `cloudx.conf` are read too), then run it for real:

   ```bash
   sudo cloudx-manage catalog sync
   ```

   Other services pick up the change within one cache period (section 5). The CLI fetches the catalog on every run, so there is no need to log in again. `CLOUDX_<SVC>_URL` (for example `CLOUDX_COMPUTE_URL`) still beats the catalog when something has to be forced.

### If it fails

What you need to go back: `/opt/cloudx.tarball`, `/root/cloudx-units.tgz` (the old units), `/root/cloudx-etc.tgz` (backup of `/etc/cloudx`), and `/root/cloudx-active-before.txt`. This procedure was tried on `infra03` (see "Test results"); `apt remove` asks for confirmation, so add `-y` when running from a script. It only applies if upgrade step 3 (moving `/opt/cloudx` to `/opt/cloudx.tarball`) has already been done on this node; if not, `/opt/cloudx` is still the running tarball and there is nothing to undo. The order matters because nginx still holds ports 8001 to 8023 as long as the package snippets exist.

1. Stop the package units and remove the packages. Use `remove`, not `purge`: `purge` also deletes `/etc/cloudx/<unit>/<unit>.conf`, which on this node is your file.

   ```bash
   sudo systemctl stop 'cloudx-*'
   sudo apt remove $(dpkg-query -W -f='${Package} ' 'cloudx-*')
   ```

2. Delete the nginx snippets (conffiles stay after `remove`) and free the ports, before the old units are started:

   ```bash
   sudo rm /etc/nginx/conf.d/cloudx-*.conf
   sudo nginx -t && sudo systemctl reload nginx
   ```

3. Put the tarball content back. The package's `/opt/cloudx` is removed first (the original tarball is safe in `/opt/cloudx.tarball`). The chain below stops if `/opt/cloudx.tarball` does not exist, so `/opt/cloudx` is not deleted without a replacement (upgrade step 3 was not done, or the rollback steps are being run out of order). If the first command fails, stop here and do not continue to step 4:

   ```bash
   sudo test -d /opt/cloudx.tarball \
        && sudo rm -rf /opt/cloudx \
        && sudo mv /opt/cloudx.tarball /opt/cloudx
   sudo ln -s /opt/cloudx/venv/bin/cloudx /usr/local/bin/cloudx   # only if this link existed before
   ```

4. Restore only the old units, then start the ones that were active:

   ```bash
   sudo tar xzf /root/cloudx-units.tgz -C / etc/systemd/system
   sudo systemctl daemon-reload
   sudo cat /root/cloudx-active-before.txt | grep -v '^cloudx-named-' | xargs -r sudo systemctl enable --now
   ```

The steps above do not touch `/etc/cloudx`, and do not extract `cloudx-etc.tgz` wholesale into `/`: that puts `cloudx.conf` back to its backed-up state. Restore only the files that really need undoing, for example:

```bash
sudo tar xzf /root/cloudx-etc.tgz -C / etc/cloudx/image-api/image-api.conf
```

nginx stays installed after the rollback and listens on port 80 with its default site, without CloudX snippets. Remove it with `sudo apt remove nginx` if you do not want it.

### Installing the packages again after a rollback

A plain `apt install` is not enough to move to packages again after a rollback. Two dpkg behaviours to expect:

- An nginx snippet that you deleted with `rm` (rollback step 2) is not restored when the package is installed again, because dpkg assumes the missing conffile was deleted on purpose.
- `deb-systemd-helper` treats a unit turned off with `systemctl disable` (upgrade step 2, on a tarball unit with the same name) as the admin's choice, so `postinst` does not start it. The package is installed but the unit is `disabled` and the external port does not answer.

Repeat upgrade steps 2 and 3, then install with the extra option and enable the units yourself:

```bash
cd ~/cloudx-debs
PKGS=$(sudo cat /root/cloudx-active-before.txt | sed -e 's/\.service$//' -e '/^cloudx-named-/d' -e '/^cloudx-storage-init$/d')
sudo apt install -o Dpkg::Options::=--force-confold -o Dpkg::Options::=--force-confmiss ./cloudx-common_*.deb $(for p in $PKGS; do echo ./${p}_*.deb; done)
sudo nginx -t && sudo systemctl restart nginx
sudo cat /root/cloudx-active-before.txt | grep -v -e '^cloudx-named-' -e '^cloudx-storage-init' | xargs -r sudo systemctl enable --now
```

`--force-confmiss` restores missing conffiles; `--force-confold` still keeps files you have changed. Do not try `deb-systemd-helper purge` to clear the unit state: the helper refuses to run outside dpkg scripts (`was not called from dpkg`).

If you already used `apt purge`, restore the deleted `/etc/cloudx/<unit>/<unit>.conf` files the same way, one by one, and do not touch `etc/cloudx/cloudx.conf`.

## 8. Limits and manual steps

| Item | Notes |
|---|---|
| Socket | gunicorn creates `/run/cloudx/<unit>.sock` with mode `0777`, so every local user on the host can connect. The reach is the same as the `0.0.0.0` TCP listener it replaces; every route still needs a token or the internal token. Do not add `www-data` to the `cloudx` group: nginx workers could then read `/etc/cloudx`, which holds secrets |
| nginx defaults | 19 non-streaming APIs use nginx defaults: `client_max_body_size 1m` and `proxy_read_timeout 60s`. For requests with a large body or that take longer, add to the `location /` block in `/etc/nginx/conf.d/cloudx-<unit>.conf`, for example `client_max_body_size 20m;` or `proxy_read_timeout 300;`, then `sudo systemctl reload nginx`. `image-api` is already an exception (`client_max_body_size 0`, `proxy_request_buffering off`, `proxy_read_timeout 3600`) |
| Four `direct` services | `object-store-api`, `metadata-api`, `metadata-proxy` and `dashboard` still listen directly on TCP. All four decide based on the client IP; behind nginx over a unix socket `request.client` is empty, and `object-store-api` treats every client as internal when there is no `X-Forwarded-For` header, which would open private buckets. A trusted-proxy model for these four is a follow-up spec. `dashboard` also uses WebSocket |
| TLS and path routing | not available yet. nginx only listens on HTTP on each external port; there is no path-based routing (`/image`) |
| `nginx -t` fails at install | installing an API package does not fail; a `warning: nginx -t failed` message is printed. Check with `sudo nginx -t`, fix it, then `sudo systemctl reload nginx`. A port still held by another process (for example an old gunicorn) makes the reload fail to bind; stop that process and run `sudo systemctl restart nginx` |
| Architecture and distro | Ubuntu 24.04 amd64 with Python 3.12 only |
| VM metadata | `169.254.169.254` is served through an `iptables` rule on the host where `metadata-proxy` runs (`scripts/setup-metadata-route.sh`), not by an agent on every compute node. VMs on other compute nodes cannot reach it; SSH keys and user-data still arrive through the config drive (`troubleshooting.md`) |
| Provider network CIDR | do not use `172.17.0.0/16` when Docker is installed: `docker0` uses `172.17.0.1/16` and breaks routing from the host (`troubleshooting.md`) |

### Host preparation the packages do not do

`install-systemd.sh` does a few things on the host that the packages do not. Do them by hand on the node concerned.

**DNS (controller, `cloudx-dns-worker`).** This package is only a `Recommends` of `cloudx-controller` because it pulls in `bind9`, and the distro's own `named` instance would fight for port 53. That is why 4.1 does not install it. Prepare the host first, then install:

```bash
sudo apt install bind9 bind9utils
sudo systemctl disable --now named bind9 || true   # on Ubuntu 24.04 one of the names may be an alias
systemctl is-active bind9                          # must be inactive
sudo mkdir -p /var/lib/cloudx/dns/auth /var/lib/cloudx/dns/resolver
echo '/var/lib/cloudx/dns/** rwk,' | sudo tee /etc/apparmor.d/local/usr.sbin.named
sudo apparmor_parser -r /etc/apparmor.d/usr.sbin.named
sudo apt install cloudx-dns-worker
```

Set `dns_public_address` (authoritative BIND) and `dns_resolver_address` (resolver BIND) in `cloudx.conf` to two IPs that are configured on that node's interface; BIND must not bind `0.0.0.0` because `systemd-resolved` uses port 53 on localhost (`dns.md`).

**Prometheus (controller).** Prometheus reads `/metrics` with the token from `metrics_token`. If Prometheus is installed on the same node:

```bash
( umask 037; sudo grep '^metrics_token' /etc/cloudx/cloudx.conf | cut -d= -f2- | tr -d ' \n' | sudo tee /etc/prometheus/metrics_token >/dev/null )
sudo chgrp prometheus /etc/prometheus/metrics_token
sudo chmod 640 /etc/prometheus/metrics_token
```

**File store and container.** A `file-store-worker` node needs `nfs-ganesha` and `nfs-ganesha-vfs`, with `sudo systemctl disable --now nfs-ganesha` (the distro instance fights for port 2049). A `container-worker` node needs `docker.io` with `/etc/docker/daemon.json` holding `{"iptables": false, "bridge": "none", "ip-forward": false}` so Docker does not touch iptables and OVN.

**LVM volume group.** `install-systemd.sh` calls `scripts/setup-storage.sh` for a local `vg-cloudx`. On a storage node, use `cloudx-setup-storage` (4.3).

## 9. The tarball still works

`scripts/install-systemd.sh` and `scripts/build-tarball.sh` (`make install-systemd`, `make package`, `make migrate`) are unchanged and still work for nodes that have not been moved. Do not use both on the same node: the units in `/etc/systemd/system` and the tarball's `/opt/cloudx` collide with the packages. Tarball nodes and package nodes can live in one cluster, because the external ports and the protocol between services have not changed. One condition: tarball code that includes the catalog lookup takes URLs from `GET /internal/catalog` in auth when `*_api_url` is missing from that node's conf. Upgrade auth first (or keep the `*_api_url` keys in the tarball nodes' conf as overrides), and fill the catalog (section 5) before starting nodes that do not have those keys.
