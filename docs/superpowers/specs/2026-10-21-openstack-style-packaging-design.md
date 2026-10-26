# OpenStack-style .deb packages and runtime for CloudX

## Goal
An operator installs and runs CloudX like OpenStack: one `.deb` package per service, one command per service (`cloudx-image-api`, `cloudx-compute-worker`), one front door (nginx) on each node, and services that find each other through the service catalog. Expected result: an infra node can be given any services through `apt install`, without `install-systemd.sh`, without editing URLs on every node.

What was learned from OpenStack (source in `/home/sandy/projects/openstack_source_code`, the October 2026 master version, plus devstack from opendev):
- APIs have no server of their own. Nova (`nova.wsgi.osapi_compute:application`), Glance (`glance.wsgi.api:application`), Keystone and Placement only expose a WSGI module. The `nova-api` command was removed in Nova 33.0.0, and a standalone `glance-api` refuses to run. The WSGI server is the operator's choice. The Nova docs mention uWSGI and gunicorn.
- The reference layout (devstack): Apache `ProxyPass` per API, one uwsgi process per API on a unix socket `/var/run/uwsgi/<name>.socket`, a systemd unit per API. Glance and Swift are excepted because they need chunked upload.
- Daemons (`nova-compute`, `nova-conductor`, the Neutron agents) are console scripts in `pyproject.toml` that call `oslo.service`.
- One INI file per service (`/etc/nova/nova.conf`), `<svc>-manage db sync` run by the operator, services find each other through the Keystone catalog, one package per service (`apt install nova-api nova-conductor ...`).
- Not readable: its Debian/Ubuntu packaging (the salsa repo gives 404). The package part here is based on the official installation guides, not on the content of `debian/`.

## CloudX today (checked in the repo and the lab, 2026-10-21)
- 43 units in `etc/systemd/`. APIs: gunicorn with `UvicornWorker` on `0.0.0.0:<port>`; daemons: `python -m app.main`; two BIND units (`cloudx-named-*`).
- Every API is a package `app` in `services/<dir>/` (they cannot be imported together in one venv). Units use `WorkingDirectory`.
- Config: `/etc/cloudx/cloudx.conf` plus `/etc/cloudx/<svc>/<svc>.conf` (`shared/config.py`), loaded into the environment. `cloudx-db-migrate` exists (`shared/migrate.py`).
- Auth is already Keystone-shaped: `/v3/services`, `/v3/endpoints`, and `catalog` in the `/auth/tokens` response.
- **The catalog is unused and wrong.** `auth/app/bootstrap.py` fills it once from the environment at first start and never updates it. In the lab: 13 services, all `http://127.0.0.1:...`, although image and block-store are now on infra02. The services key-manager, k8s-engine, dns, file-store, container, database, messaging, reservation and ha are not registered.
- 38 modules read `*_API_URL` straight from the environment. The CLI reads `CLOUDX_<SVC>_URL` or the default `127.0.0.1:<port>`, and does not read the catalog.
- Four services use the client IP (`object_store/app/main.py:_classify_source`, `dashboard/app/main.py`, `metadata/app/metadata_service.py`, `metadata_proxy/app/main.py`). See the Security section.
- The code in the lab is already in `/opt/cloudx/{venv,services,cli}`, as the packages will use it.

## Decisions

### 1. A single manifest: `packaging/manifest.toml`
One list of units is the source for packages, units, nginx snippets and the consistency tests. TOML format (`tomllib` is in Python 3.12). Per unit:

| Field | Content |
|---|---|
| `name` | `image-api`; package name `cloudx-image-api`, unit `cloudx-image-api.service`, command `/usr/bin/cloudx-image-api` |
| `kind` | `api`, `daemon`, or `unit` (BIND, copied as it is) |
| `dir` | the directory in `services/` (`image`) |
| `port`, `workers` | `api` only; `workers` defaults to the current `-w` value of the unit |
| `frontend` | `nginx` or `direct` (see 4) |
| `streaming` | `true` for big uploads/downloads (only `image-api`) |
| `catalog_name`, `catalog_type`, `url_key` | catalog registration (`image`, `image`, `image_api_url`) |
| `roles` | `controller`, `compute`, `storage` (forms the meta packages) |
| `depends` | extra apt packages (for example `bind9` for dns-worker) |

`daemon` and `unit` units keep using the files in `etc/systemd/` (After=, user, capabilities are not reinvented); the generator only replaces `ExecStart` with the wrapper. `api` units are generated from a template. The tarball and `install-systemd.sh` keep working until replaced; `etc/systemd/` is not changed.

### 2. Layout and packages (target: Ubuntu 24.04, amd64)
The location stays `/opt/cloudx` (venv, `services/`, `cli/`), the same as the current lab nodes, so the upgrade path is short.

| Package | Content |
|---|---|
| `cloudx-common` | `/opt/cloudx/venv` (dependencies + `shared`), user `cloudx`, `/etc/cloudx/cloudx.conf` (created if missing, secrets generated like `install-systemd.sh`), `/var/lib/cloudx`, tmpfiles for `/run/cloudx`, `/usr/bin/cloudx-manage`, `/usr/share/cloudx/manifest.toml` |
| `cloudx-db` | Alembic migrations and `cloudx-manage db sync`; does not run automatically |
| `cloudx-<unit>` (one per unit) | the code of `services/<dir>/`, the systemd unit, the wrapper `/usr/bin/cloudx-<unit>`, `/etc/cloudx/<unit>/<unit>.conf` (conffile), and for nginx-style `api` units also `/etc/nginx/conf.d/cloudx-<unit>.conf` |
| `cloudx-controller`, `cloudx-compute`, `cloudx-storage` | meta, `Depends` on the packages with the matching `roles`; `Recommends` PostgreSQL, RabbitMQ, memcached, OVN (not configured by the packages) |
| `cloudx-cli` | the `cloudx` CLI with its own small venv |

All per-unit packages `Depends: cloudx-common (= version)`. The version comes from `VERSION`, revision `-1`. The venv is built at build time at the final path `/opt/cloudx/venv`, shebangs are rewritten, `Depends: python3 (>= 3.12), python3 (<< 3.13)`. The current host scripts (`setup-compute-node.sh`, `setup-ceph-libvirt-secret.sh`) are installed as `/usr/sbin/cloudx-setup-compute` and `/usr/sbin/cloudx-setup-ceph-secret` in `cloudx-compute`, and are not run automatically because they need IPs and a keyring.

Installation behaviour follows OpenStack on Ubuntu: units are enabled and started at install (respecting `policy-rc.d`); the operator fills in the config, runs `cloudx-manage db sync`, then restarts. An upgrade that brings new migrations makes the service refuse to start until `db sync`, as today.

### 3. API runtime: `shared/launcher.py`
The wrapper `/usr/bin/cloudx-<unit>` only calls `shared.launcher.run_api(name, app_dir, port, workers, frontend)`, which loads the config (`load_service_config`), then `execv`s gunicorn with `-k uvicorn.workers.UvicornWorker`.
- `workers`: priority order `<unit>.conf` > `cloudx.conf` > manifest. The `workers` key in the conf replaces the `-w` that is currently baked into the unit.
- Bind: `frontend=nginx` uses `unix:/run/cloudx/<unit>.sock`; `frontend=direct` uses `0.0.0.0:<port>` as today.
- Unit: `User=` from the manifest, `Group=cloudx`, `UMask=0007` (for files the service creates; the socket is created by gunicorn with mode 0777 and the nginx workers depend on that), `Restart=always`, `RestartSec=5s`.
- **`--forwarded-allow-ips` must not be `*`.** Uvicorn with `*` takes the first `X-Forwarded-For` entry as the client, and that entry is sent by the client (it can be forged). For `frontend=nginx` the value is empty so the header is ignored.
- Gunicorn stays: OpenStack names it a valid server, and there is no technical reason to replace it. Switching to `uvicorn --workers` would only change the launcher.
- Daemons: the wrapper calls `run_daemon(name, app_dir)`, which `chdir`s to `app_dir` and then runs `app.main` (`runpy`), so the unit only contains `ExecStart=/usr/bin/cloudx-<unit>`.

The package name `app` is not changed. Renaming to `cloudx_<svc>` touches every import and test (the tests import `services.<dir>.app...`) and the only benefit is tidiness; postponed.

### 4. Front door: nginx
One snippet per API in `/etc/nginx/conf.d/cloudx-<unit>.conf`, an editable conffile:
- an `upstream` to the unix socket, `server { listen <port>; location / { proxy_pass ...; } }`, `proxy_http_version 1.1`, `proxy_set_header Host`, and `X-Forwarded-For $remote_addr` (overwrites, does not append).
- `streaming = true`: `client_max_body_size 0`, `proxy_request_buffering off`, `proxy_read_timeout 3600` (the equivalent of the chunked exception for Glance and Swift in devstack).
- The postinst runs `nginx -t`, then reloads nginx; if `nginx -t` fails, the package stays installed and the error message is printed.
- The external ports are the same as now, so `*_api_url`, the CLI and the dashboard do not change. Path-based routing (`/image`) is not included: it needs `root_path` in every application and risks breaking links in responses.

`frontend = "nginx"` for 20 APIs: auth 8005, billing 8011, block-store 8004, compute 8003, container 8018, database 8019, dns 8016, event 8012, file-store 8017, ha 8020, image 8002, k8s-engine 8015, key-manager 8014, load-balancer 8008, messaging 8022, networking 8001, orchestration 8013, placement 8006, reservation 8023, telemetry 8010.

`frontend = "direct"` (still TCP, no nginx) for 4 services: object-store 8007, metadata-api 8775, metadata-proxy 8776, dashboard 8080. Reason: all four use the client IP (see Security); the dashboard also uses WebSocket.

### 5. Daemons
A console script per unit (see 3). CloudX daemons are a single process and do not need an `oslo.service`-style launcher. `oslo.config` configuration is not imitated: rewriting config loading for options registered in code is costly and the benefit is small.

### 6. Service catalog
**Source of truth.** `shared/services.py` holds the table (`catalog_name`, `catalog_type`, `url_key`, default port). `auth/app/config.py` uses it in place of the truncated `_CLOUDX_SERVICES`. A test makes sure the table and the manifest agree.

**Filling the catalog.** `cloudx-manage catalog sync [--dry-run]` reads the `url_key` values from the conf, then adds services and updates endpoints (`public` and `internal`) whose URL differs; it prints every change. An optional key `<url_key>_public` (for example `image_api_public_url`) fills the `public` endpoint when it differs from `internal`. The initial bootstrap in auth uses the same function to create new entries. Existing entries are changed only by `sync`, not by a normal start, so an admin's changes are not silently overwritten.

**Reading the catalog.** `shared/catalog.py`: `url(service, interface="internal")` resolves in this order: (1) the key `<url_key>` in the conf or environment if present (an override, the equivalent of `endpoint_override` in keystoneauth); (2) the catalog from auth through `GET /internal/catalog` (header `X-Internal-Token`, following the `/internal/*` convention), cached for `catalog_cache_ttl` (default 60 s), and refreshed once if the connection to the URL it gave fails; (3) a `CatalogError` that names the service. `auth_api_url` stays required in the conf (the starting point, like `auth_url` in OpenStack).

**Clients.** The CLI uses the `catalog` from the login response (the `public` interface), with `CLOUDX_<SVC>_URL` as an override and `127.0.0.1:<port>` only when the catalog does not contain that service.

**Adoption.** The override-first order means nothing changes until a URL is taken out of the conf: safe and reversible per step. After the 38 modules move to `shared.catalog`, the default conf contains only `auth_api_url`, and a test fails if a module outside `shared/catalog.py` (and the auth bootstrap) reads `*_API_URL` directly.

### 7. `cloudx-manage`
One command for the operator, following OpenStack's `<svc>-manage`: `cloudx-manage db sync` (wraps `shared.migrate`), `cloudx-manage catalog sync`. `cloudx-db-migrate` stays as an alias.

### 8. Build
`scripts/build-deb.sh` (target `make deb`) reads the manifest, builds a staging tree per package, calls `fakeroot dpkg-deb --build`, and writes the result to `dist/deb/`. Building an apt archive and signing are not included; for a lab `dpkg-scanpackages` into a local repo is enough (explained in the document). `scripts/build-tarball.sh` is not changed.

## Security: client IP behind a proxy
Behind nginx over a unix socket, `request.client` is empty. `object_store/app/main.py:_classify_source` then assumes `src_ip = 127.0.0.1` and trusts `X-Forwarded-For`; if that header is missing, every client is considered INTERNAL and private buckets open. Fixing it properly needs a "trusted proxy" model (the rightmost hop that is not a trusted proxy is the client), and the dashboard-to-object-store chain adds one more hop that has to be passed. That is a behaviour change separate from this packaging, so:
- object-store, dashboard, metadata-api and metadata-proxy stay `direct` in the first release.
- Follow-up (its own spec): a trusted-proxy model for the four, then move them to nginx.
- For the 20 APIs behind nginx: checked, none reads the client IP (`grep request.client` only finds the four services above).

## Not included
TLS in nginx (now only a config change, a separate spec); path-based routing; an apt archive and signing; renaming the `app` package; `oslo.config`-style config; PostgreSQL/RabbitMQ HA; distributions or architectures other than Ubuntu 24.04 amd64; a venv per service; containers; full compliance with the Debian archive policy (bundled venv and `/opt`, not meant for the official Debian archive).

## Testing
- **Unit:** the manifest is valid (unique names, unique ports, `url_key` present in the catalog table); the unit and nginx snippet generators (golden files: `streaming`, `frontend=direct` without a snippet); the launcher argv (the `workers` priority order, unix vs TCP bind, `--forwarded-allow-ips` never `*`); catalog resolution (override, cache, refresh once, `CatalogError`); `catalog sync` (adds, updates, `--dry-run`, does not touch an identical entry); `shared/services.py` equals the manifest.
- **Package:** `dpkg-deb --info/--contents` on the build result: files present, `Depends` correct, conffiles marked, no unit in `/etc/systemd/system`.
- **Lab (a clean Ubuntu 24.04 node or VM):** install `cloudx-common`, `cloudx-db` and two API packages; `db sync`; both APIs answer on the external port through nginx; the units are alive after a reboot; the catalog contains the right URLs after `catalog sync`; `cloudx image list` from another node through the catalog without environment variables.
- The full suite and lint stay green in every phase.

## Phases (each can be released on its own)
1. **Foundation:** manifest, `launcher`, `cloudx-manage`, `build-deb.sh`, the packages `cloudx-common`/`cloudx-db`/per unit, all `frontend=direct` (the same runtime behaviour as today).
2. **Catalog:** `shared/services.py`, `catalog sync`, `/internal/catalog`, the CLI reads the catalog. Useful on its own because the lab catalog is wrong today.
3. **nginx and sockets** for 20 APIs (flipping `frontend` in the manifest).
4. **Meta packages, `cloudx-cli`, the document `docs/deb-install.md`, the lab node upgrade runbook.**
5. **Catalog adoption** in 38 modules and the lint test; URLs leave the default conf.
6. *(separate spec)* trusted-proxy for object-store, dashboard and metadata.

Phase 2 does not depend on 1. Phases 3 and 4 depend on 1. Phase 5 depends on 2.

## Risks and handling
- **Mis-grouping client IPs:** handled with `direct` for four services and the launcher test (never `*`).
- **A venv cannot be moved:** it is built at the final path, shebangs are rewritten; the lab test runs `import shared` from the installed package. The venv is tied to Python 3.12 (`Depends`).
- **Old units override the package units:** nodes installed from the tarball have `/etc/systemd/system/cloudx-*.service`, which beat `/lib/systemd/system`. The upgrade runbook requires deleting the old units and `systemctl disable --now` before installing the packages; the `/etc/cloudx/*` conffiles are kept (`--force-confold`).
- **nginx and gunicorn fighting for ports during migration:** the runbook stops the old units first; the new nginx is reloaded after that.
- **A service starting before it is configured:** `Restart=always`, as today; the package prints the next steps (fill in the config, `db sync`, restart).
- **The socket can be connected to by anyone on the host:** gunicorn creates the socket with mode 0777, so its reach equals the TCP listener it replaces; every route still needs a token or the internal token. A future `--umask` option would need another mechanism, not adding `www-data` to the `cloudx` group (the nginx workers could then read `/etc/cloudx`, which holds secrets).
