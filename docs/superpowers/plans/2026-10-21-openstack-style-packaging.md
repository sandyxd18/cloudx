# OpenStack-style .deb packages and runtime for CloudX: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An operator installs CloudX with `apt install cloudx-<unit>`, runs each service through its own command behind nginx, and services find each other through the service catalog.

**Architecture:** One manifest (`packaging/manifest.toml`) produces the `.deb` packages, the systemd units, the `/usr/bin/cloudx-<unit>` wrappers and the nginx snippets. The wrapper calls `shared/launcher.py` (gunicorn + UvicornWorker; a unix socket for APIs behind nginx). The auth catalog is filled through `cloudx-manage catalog sync` and read through `shared/catalog.py`; URLs in the conf stay as overrides.

**Tech Stack:** Python 3.12, FastAPI, gunicorn, nginx, `dpkg-deb`/`fakeroot`, pytest, Ubuntu 24.04 amd64.

**Spec:** `docs/superpowers/specs/2026-10-21-openstack-style-packaging-design.md`. Read the spec first; the plan only holds decisions that are not there.

## Global Constraints
- Target: Ubuntu 24.04, amd64 only. `Depends: python3 (>= 3.12), python3 (<< 3.13)`.
- The location stays `/opt/cloudx/{venv,services,cli}`. External ports do not change. The Python package name `app` is not changed.
- Package version = the content of `VERSION` + `-1`. `etc/systemd/`, `scripts/install-systemd.sh` and `scripts/build-tarball.sh` are not changed.
- `frontend=nginx` for 20 APIs (auth, billing, block-store, compute, container, database, dns, event, file-store, ha, image, k8s-engine, key-manager, load-balancer, messaging, networking, orchestration, placement, reservation, telemetry). `frontend=direct` for object-store, metadata-api, metadata-proxy, dashboard.
- `--forwarded-allow-ips` is never `*`; for `frontend=nginx` the value is empty. nginx uses `proxy_set_header X-Forwarded-For $remote_addr` (overwrites).
- `workers`: `<unit>.conf` > `cloudx.conf` > the manifest value. nginx APIs: `User=` from the manifest, `Group=cloudx`, `UMask=0007`, `Restart=always`, `RestartSec=5s`.
- Service URL resolution: (1) the key `<url_key>` in the conf/env, (2) the catalog from auth (`GET /internal/catalog`, cache `catalog_cache_ttl`, default 60 s), (3) a `CatalogError` that names the service. `auth_api_url` always comes from the conf.
- `cloudx-db-migrate` stays as an alias of `cloudx-manage db sync`.
- Every task ends with a green `pytest -q` and a clean `ruff check services cli tests --select F401,F811,F821 --exclude "node_modules,migrations/versions"`. Commits end with the line `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus
1. Auth/catalog unreachable while a URL is resolved: use the old cache if there is one, otherwise a `CatalogError` that names the service (Task 9).
2. `workers` holding `0`, `-1`, `abc`, or empty: the launcher stops with a message that names the unit and the key, not a gunicorn traceback (Task 2).
3. Reinstalling or upgrading a package must not regenerate secrets that already exist (`auth_secret_key`, `internal_token`, and the others): every user's tokens would be cancelled (Task 3, used by the postinst in Task 5).
4. `ha_api_url` holding several comma-separated URLs: `catalog sync` registers the first one, and the override is returned as it is (Tasks 7 and 9).
5. The CLI uses `CLOUDX_TOKEN` only, or an old auth without the catalog route: it still works with env/defaults, without a crash (Task 10).

---

## Phase 1: Foundation

### Task 1: The manifest and its loader

**Files:**
- Create: `packaging/manifest.toml`, `scripts/cloudx_deb/__init__.py`, `scripts/cloudx_deb/manifest.py`, `tests/packaging/test_manifest.py`
- Modify: `pytest.ini` (`pythonpath = . services scripts`)

**Interfaces:**
- Produces: `class ManifestError(ValueError)`; `@dataclass(frozen=True) class Unit` with the fields `name: str, kind: str, dir: str | None, port: int | None, workers: int | None, frontend: str | None, streaming: bool, catalog: str | None, roles: tuple[str, ...], depends: tuple[str, ...], bundle_with: str | None`; properties `Unit.package -> "cloudx-<name>"`, `Unit.unit_file -> "cloudx-<name>.service"`; `load_manifest(path: Path = DEFAULT_MANIFEST) -> list[Unit]`.

- [ ] **Step 1: Write the tests** (`tests/packaging/test_manifest.py`):
  - `test_every_systemd_unit_has_one_manifest_entry`: the set of `cloudx-<name>.service` from the manifest == `etc/systemd/*.service`.
  - `test_api_fields_match_the_current_unit`: for every `kind=api`, `dir` == the basename of the unit's `WorkingDirectory=`, `port` == the number in `--bind 0.0.0.0:<port>` or `--port <port>`, `workers` == the `-w` number (dashboard, without `-w`: 1).
  - `test_names_and_ports_are_unique`.
  - `test_frontend_split`: `{u.name for u in units if u.frontend == "nginx"}` equals the 20 names in Global Constraints (the `-api` suffix is removed from the manifest name, e.g. `image-api`); `direct` equals the four names.
  - `test_roles`: `controller`, `compute`, `storage` hold exactly the list in Step 3.
  - `test_bad_manifest_raises`: two entries with the same name, a `frontend` other than `nginx|direct`, a `kind` other than `api|daemon|unit` raise `ManifestError`.
- [ ] **Step 2: Run, make sure they fail:** `pytest tests/packaging/test_manifest.py -q` → import error.
- [ ] **Step 3: Write `packaging/manifest.toml` and `manifest.py`.** One `[[unit]]` per file in `etc/systemd/`; `name` = the unit name without `cloudx-`. All `frontend = "direct"` in this phase (flipped in Task 11), except the ones that are clearly already `direct` (4 services). `streaming = true` only for `image-api`. `catalog` = the catalog name (Task 6) for the APIs that have one; metadata-proxy and dashboard without. `bundle_with = "dns-worker"` for `named-auth` and `named-resolver` (kind `unit`); `depends = ["bind9"]` on `dns-worker`. Roles: `controller` = all `*-api` (including dashboard), `conductor`, `reaper`, `image-worker`, `billing-worker`, `event-worker`, `orchestration-worker`, `k8s-engine-worker`, `dns-worker`; `storage` = `block-store-worker`; `compute` = `compute-worker`, `metadata-proxy`, `telemetry-worker`, `lb-worker`, `vpn-worker`, `bgp-worker`, `ha-watchdog`. `container-worker` and `file-store-worker` have no role (they need a configured host). Parse with `tomllib`, validate in `load_manifest`.
- [ ] **Step 4: Run the tests** → PASS. Then `pytest -q`.
- [ ] **Step 5: Commit** `feat(packaging): manifest of CloudX units`.

### Task 2: Launcher

**Files:**
- Create: `services/shared/launcher.py`, `tests/shared/test_launcher.py`

**Interfaces:**
- Consumes: `shared.config.load_service_config(name) -> dict`.
- Produces: `resolve_workers(unit: str, default: int, config: Mapping[str, str]) -> int` (raises `ValueError` with the message `"<unit>: workers must be a positive integer, got '<value>'"`); `gunicorn_argv(app_dir: str, bind: str, workers: int, frontend: str) -> list[str]`; `run_api(name: str, app_dir: str, port: int, workers: int, frontend: str) -> NoReturn`; `run_daemon(name: str, app_dir: str) -> NoReturn`.

- [ ] **Step 1: Write the tests** (`tests/shared/test_launcher.py`):
  - `test_workers_precedence`: config `{"workers": "3"}` beats the default 2; without the key → 2.
  - `test_bad_workers_values` (parametrize `"0"`, `"-1"`, `"abc"`, `""`): `ValueError` with the text `workers must be a positive integer`.
  - `test_nginx_binds_a_unix_socket_and_trusts_no_forwarded_ips`: `gunicorn_argv("/opt/cloudx/services/image", "unix:/run/cloudx/image-api.sock", 2, "nginx")` contains `-k uvicorn.workers.UvicornWorker`, `-w 2`, `--bind unix:...`, `--chdir`, `app.main:app` as the last element, `--forwarded-allow-ips=` and does not contain `*`.
  - `test_direct_binds_tcp_and_never_passes_a_wildcard_for_forwarded_ips`.
  - `test_run_daemon_changes_directory_and_runs_app_main`: `monkeypatch` `os.chdir` and `runpy.run_module`; the call `("app.main", run_name="__main__")` happens after chdir.
  - `test_client_supplied_x_forwarded_for_is_ignored_on_a_unix_socket` (integration, skipped when `gunicorn` or `curl` is missing): run a small ASGI app in `tmp_path` using the argv from `gunicorn_argv(..., "nginx")`, `curl --unix-socket ... -H 'X-Forwarded-For: 6.6.6.6'`; the app returns `scope["client"]`; assert the result does not contain `6.6.6.6`.
- [ ] **Step 2: Run, make sure they fail.** `pytest tests/shared/test_launcher.py -q`.
- [ ] **Step 3: Implement.** `run_api` loads `load_service_config(name)`, `resolve_workers`, chooses the bind (`unix:/run/cloudx/<name>.sock` for `nginx`, `0.0.0.0:<port>` for `direct`), then `os.execv` to gunicorn in the same directory as `sys.executable`. A ValueError from `resolve_workers` is printed to stderr and exits `2`. `run_daemon`: `load_service_config(name)`, `os.chdir(app_dir)`, `sys.path.insert(0, app_dir)`, `runpy.run_module("app.main", run_name="__main__")`.
- [ ] **Step 4: Run the tests** → PASS (the integration test when gunicorn is in the venv).
- [ ] **Step 5: Commit** `feat(shared): launcher for CloudX APIs and daemons`.

### Task 3: `cloudx-manage` (db sync, conf init)

**Files:**
- Create: `services/shared/manage.py`, `tests/shared/test_manage.py`
- Modify: `services/pyproject.toml` (`[project.scripts]`: `cloudx-manage = "shared.manage:main"`; `cloudx-db-migrate` stays)

**Interfaces:**
- Consumes: `shared.migrate.main(argv: list[str] | None = None) -> int`.
- Produces: `main(argv: list[str] | None = None) -> int` with the subcommands `db sync [service ...]`, `conf init [--path PATH] [--sample PATH]`; `init_conf(path: Path, sample: Path) -> list[str]` (the list of keys that were generated).

- [ ] **Step 1: Write the tests:**
  - `test_db_sync_delegates_to_migrate`: `monkeypatch` `shared.migrate.main`; `main(["db","sync","compute"])` calls it with `["compute"]`.
  - `test_conf_init_creates_missing_conf_with_generated_secrets`: the file does not exist → it is created from the sample, mode `0o640`, the five keys (`auth_secret_key`, `auth_admin_password`, `key_manager_master_key`, `metrics_token`, `internal_token`) are filled.
  - `test_conf_init_never_regenerates_existing_secrets` (Review Focus 3): run twice; the content of `auth_secret_key` is identical, the second return is `[]`; an empty key added later is filled without touching the others.
- [ ] **Step 2: Run, make sure they fail.**
- [ ] **Step 3: Implement.** The `init_conf` logic follows the `gen()` function in `scripts/install-systemd.sh:87-96` (32-byte hex for `auth_secret_key` and `key_manager_master_key`, 24 for tokens, base64 18 characters without `/+=` for the admin password) using `secrets`; read/write without `configparser` breaking comments (replace lines, insert below `[DEFAULT]`).
- [ ] **Step 4: Run the tests** → PASS.
- [ ] **Step 5: Commit** `feat(shared): cloudx-manage with db sync and conf init`.

### Task 4: Unit renderer, wrapper, nginx snippet

**Files:**
- Create: `scripts/cloudx_deb/render.py`, `tests/packaging/test_render.py`, `tests/packaging/golden/` (golden files)

**Interfaces:**
- Consumes: `Unit` (Task 1).
- Produces: `render_wrapper(u: Unit) -> str` (a `#!/opt/cloudx/venv/bin/python` script that calls `run_api(...)` or `run_daemon(...)`); `render_api_unit(u: Unit) -> str`; `render_daemon_unit(u: Unit, source: str) -> str` (copies `source` from `etc/systemd/`, replacing only the `ExecStart=` line with `/usr/bin/cloudx-<name>`); `render_nginx(u: Unit) -> str`.

- [ ] **Step 1: Write the tests** with golden files for `image-api` (nginx, streaming), `auth-api` (nginx), `object-store-api` (direct), `compute-worker` (daemon):
  - The nginx API unit contains `Group=cloudx`, `UMask=0007`, `Restart=always`, `RestartSec=5s`, `ExecStart=/usr/bin/cloudx-auth-api`; a unit for user root (`block-store-api`) keeps `User=root` with `Group=cloudx`.
  - The nginx snippet: `listen 8002;`, `proxy_pass http://unix:/run/cloudx/image-api.sock`, `proxy_set_header X-Forwarded-For $remote_addr;` (and no `$proxy_add_x_forwarded_for`), `proxy_http_version 1.1`; only image-api contains `client_max_body_size 0`, `proxy_request_buffering off`, `proxy_read_timeout 3600`.
  - `render_nginx` for a `direct` unit raises `ValueError` (there is no snippet).
  - The daemon unit is identical to its source except for `ExecStart=`.
- [ ] **Step 2: Run, make sure they fail.**
- [ ] **Step 3: Implement** the four functions. Update the golden files deliberately (the tests compare; a small `--update-golden` script is not needed, write the golden files from the first output that has been checked by eye).
- [ ] **Step 4: Run the tests** → PASS.
- [ ] **Step 5: Commit** `feat(packaging): render units, wrappers and nginx snippets`.

### Task 5: The package builder

**Files:**
- Create: `scripts/cloudx_deb/build.py`, `scripts/build-deb.sh`, `tests/packaging/test_build.py`
- Modify: `Makefile` (target `deb`), `.gitignore` (`dist/deb/`, `build/deb/` if not already covered)

**Interfaces:**
- Consumes: `load_manifest`, `render_*`.
- Produces: `build_all(out_dir: Path, version: str, *, with_venv: bool = True, only: list[str] | None = None) -> list[Path]` (the `.deb` results); `control_file(pkg: str, version: str, depends: list[str], description: str) -> str`.

- [ ] **Step 1: Write the tests** (skipped when `dpkg-deb` is missing; use `with_venv=False`, `only=["common","db","image-api","compute-worker"]`):
  - `test_packages_have_the_expected_names_and_versions`: `dpkg-deb --field` gives `Package: cloudx-image-api`, `Version: <VERSION>-1`, `Architecture: amd64`.
  - `test_unit_package_contents`: `dpkg-deb --contents` contains `/lib/systemd/system/cloudx-image-api.service`, `/usr/bin/cloudx-image-api`, `/opt/cloudx/services/image/app/main.py`, `/etc/cloudx/image-api/image-api.conf`; no `/etc/systemd/system/`, no `__pycache__`, no `tests/`, no `migrations/`.
  - `test_conffiles_are_declared`: `conffiles` contains `/etc/cloudx/image-api/image-api.conf` (the nginx snippet comes after Task 11).
  - `test_depends`: `cloudx-image-api` `Depends: cloudx-common (= <VERSION>-1)`; `cloudx-common` contains `python3 (>= 3.12), python3 (<< 3.13)`; `cloudx-dns-worker` contains `bind9`.
  - `test_db_package_owns_all_migrations_and_units_do_not`: `cloudx-db` contains `/opt/cloudx/services/migrations/registry.py` and `/opt/cloudx/services/image/migrations/`; no file is owned by two packages.
  - `test_postinst_enables_and_starts_via_systemd_helpers`: the unit's `postinst` script passes `sh -n`, contains `deb-systemd-helper` and `deb-systemd-invoke`; the `cloudx-common` `postinst` calls `cloudx-manage conf init`.
- [ ] **Step 2: Run, make sure they fail.**
- [ ] **Step 3: Implement.** `cloudx-common`: the venv is built in `<staging>/opt/cloudx/venv` from `requirements.txt` plus `pip install --no-deps services/` (like `install-systemd.sh:167-170`), then the staging prefix is rewritten to `/opt/cloudx/venv` in `bin/*` and `pyvenv.cfg`; the sample `etc/cloudx/cloudx.conf.sample` goes to `/usr/share/cloudx/`; `tmpfiles.d` for `/run/cloudx` (`0755 cloudx cloudx`); `postinst`: create the user `cloudx`, `/var/lib/cloudx`, `cloudx-manage conf init --path /etc/cloudx/cloudx.conf --sample /usr/share/cloudx/cloudx.conf.sample`. Unit packages: copy `services/<dir>` without `migrations/`, `tests/`, `__pycache__`, `frontend/`, `node_modules`; the conf from `etc/cloudx/<name>/<name>.conf.sample` if it exists, otherwise `[DEFAULT]`. `cloudx-db`: `services/migrations/` and `services/*/migrations/`. Build with `fakeroot dpkg-deb --build`. `scripts/build-deb.sh` calls `python3 -c "from cloudx_deb.build import build_all..."` with `VERSION`; `make deb`.
- [ ] **Step 4: Run the tests** → PASS. Then `pytest -q`.
- [ ] **Step 5: Verify on a clean node** (a node or Ubuntu 24.04 VM without `/opt/cloudx`; if there is none, upload an Ubuntu 24.04 cloud image to the lab and boot a VM): `make deb`, copy the result, `apt install ./cloudx-common_*.deb ./cloudx-db_*.deb ./cloudx-image-api_*.deb`; expected result: `/opt/cloudx/venv/bin/python -c "import shared"` succeeds, `systemctl is-active cloudx-image-api` is `active` (needs PostgreSQL: point `database_url` at the lab, run `cloudx-manage db sync`), `curl http://127.0.0.1:8002/health` gives 200 (the frontend is still `direct`).
- [ ] **Step 6: Commit** `feat(packaging): build .deb packages from the manifest`.

---

## Phase 2: Catalog

### Task 6: A single catalog table

**Files:**
- Create: `services/shared/services.py`, `tests/shared/test_services_table.py`
- Modify: `services/auth/app/config.py` (remove the `_CLOUDX_SERVICES` literal, replace it with `build_catalog_services()`)

**Interfaces:**
- Produces: `@dataclass(frozen=True) class CatalogService: name, type, url_key, port`; `SERVICES: tuple[CatalogService, ...]` (22 rows: identity/identity/`auth_api_url`/8005; compute/compute/`compute_api_url`/8003; image/image/`image_api_url`/8002; network/network/`networking_api_url`/8001; volume/block-storage/`block_store_api_url`/8004; placement/placement/`placement_api_url`/8006; object-store/object-store/`object_store_api_url`/8007; load-balancer/load-balancer/`load_balancer_api_url`/8008; metadata/metadata/`metadata_api_url`/8775; telemetry/telemetry/`telemetry_api_url`/8010; billing/billing/`billing_api_url`/8011; event/event/`event_api_url`/8012; orchestration/orchestration/`orchestration_api_url`/8013; key-manager/key-manager/`key_manager_api_url`/8014; k8s-engine/k8s-engine/`k8s_engine_api_url`/8015; dns/dns/`dns_api_url`/8016; file-store/file-store/`file_store_api_url`/8017; container/container/`container_api_url`/8018; database/database/`database_api_url`/8019; ha/ha/`ha_api_url`/8020; messaging/messaging/`messaging_api_url`/8022; reservation/reservation/`reservation_api_url`/8023); `by_name(name: str) -> CatalogService` (`KeyError` if missing); `public_key(url_key: str) -> str` (`image_api_url` → `image_api_public_url`).

- [ ] **Step 1: Write the tests:** `test_table_matches_manifest` (every `Unit.catalog` is in the table, and `port` equals `CatalogService.port`; every table entry has one `api` manifest unit); `test_names_unique`; `test_public_key`; `test_auth_bootstrap_registers_every_service` (`auth.app.config._CLOUDX_SERVICES` contains every table `name` with the `url` from the env `url_key.upper()` or `http://127.0.0.1:<port>`).
- [ ] **Step 2: Run, make sure they fail.**
- [ ] **Step 3: Implement** the table and `_CLOUDX_SERVICES` built from it (the default `http://127.0.0.1:<port>` replaces `http://<name>-api:8000`).
- [ ] **Step 4: Run the tests** → PASS; the existing auth tests stay green.
- [ ] **Step 5: Commit** `feat(auth): one service table for the catalog`.

### Task 7: `catalog sync`

**Files:**
- Create: `services/auth/app/catalog_sync.py`, `tests/auth/test_catalog_sync.py`
- Modify: `services/shared/manage.py` (subcommand `catalog sync [--dry-run]`), `services/auth/app/bootstrap.py` (`_bootstrap_service_catalog` uses `sync_catalog(..., create_only=True)`)

**Interfaces:**
- Consumes: `shared.services.SERVICES`, `public_key`; `auth.app.models.ServiceCatalogEntry`, `ServiceEndpoint`.
- Produces: `sync_catalog(db: Session, urls: Mapping[str, str], *, dry_run: bool = False, create_only: bool = False) -> list[str]` (one line per change, e.g. `+ image public http://...`, `~ image internal http://old -> http://new`); `urls` maps the `url_key` (lowercase) to the URL.

- [ ] **Step 1: Write the tests** (in-memory SQLite like the existing `tests/auth/`):
  - `test_adds_missing_services_with_public_and_internal_endpoints`.
  - `test_updates_changed_urls_and_reports_each_change`.
  - `test_second_run_is_a_no_op` (returns `[]`).
  - `test_keys_missing_from_conf_are_left_alone` (deletes nothing).
  - `test_public_override_key`: `image_api_public_url` fills the `public` endpoint only.
  - `test_comma_list_registers_the_first_url` (Review Focus 4): `ha_api_url = http://a:8020,http://b:8020` → `http://a:8020`.
  - `test_dry_run_changes_nothing`; `test_create_only_never_updates_existing`.
- [ ] **Step 2: Run, make sure they fail.**
- [ ] **Step 3: Implement** `sync_catalog`; `catalog sync` in `manage.py` loads `load_service_config("auth-api")`, puts the `services/` directory in `sys.path` (the way `shared/migrate.py` does), opens `auth.app.db.SessionLocal`, prints the change lines; `--dry-run` does not commit.
- [ ] **Step 4: Run the tests** → PASS.
- [ ] **Step 5: Commit** `feat(auth): cloudx-manage catalog sync`.

### Task 8: Catalog routes in auth

**Files:**
- Modify: `services/auth/app/routers/catalog.py`
- Test: `tests/auth/test_catalog_routes.py`

**Interfaces:**
- Consumes: `auth.app.tokens._build_catalog(db) -> list`; `shared.auth_middleware.require_internal`.
- Produces: `GET /v3/auth/catalog` (logged-in user) → `{"catalog": [...]}`; `GET /internal/catalog` (`X-Internal-Token` or admin) → `{"catalog": [...]}`.

- [ ] **Step 1: Write the tests** (the `TestClient` pattern of the other auth tests): both routes return the `image` entry with an `internal` endpoint; `/v3/auth/catalog` without a token → 401; `/internal/catalog` with a wrong `INTERNAL_TOKEN` → 401/403 (when the token is set), with the right token → 200.
- [ ] **Step 2: Run, make sure they fail.**
- [ ] **Step 3: Implement** the two routes using `_build_catalog`.
- [ ] **Step 4: Run the tests** → PASS.
- [ ] **Step 5: Commit** `feat(auth): catalog routes for users and services`.

### Task 9: The catalog client `shared/catalog.py`

**Files:**
- Create: `services/shared/catalog.py`, `tests/shared/test_catalog_client.py`

**Interfaces:**
- Consumes: `shared.services.by_name`; `shared.internal.internal_headers() -> dict`.
- Produces: `class CatalogError(RuntimeError)`; `url(service: str, interface: str = "internal") -> str`; `invalidate() -> None`; `request(method: str, service: str, path: str, **kwargs) -> requests.Response`.

- [ ] **Step 1: Write the tests** (`monkeypatch` `requests.get`/`requests.request` and `time.monotonic`):
  - `test_override_wins`: `IMAGE_API_URL` in the env → returned as it is, without a network call.
  - `test_ha_override_with_several_urls_is_returned_verbatim` (Review Focus 4).
  - `test_resolves_from_the_catalog_and_caches_for_the_ttl`: one HTTP call for two `url()`; after the TTL, a second call.
  - `test_unreachable_auth_serves_the_stale_cache` and `test_unreachable_auth_without_cache_raises_catalog_error_naming_the_service` (Review Focus 1).
  - `test_unknown_service_or_interface_raises_catalog_error`.
  - `test_request_retries_once_when_the_catalog_url_changed`: `ConnectionError` on the old URL → `invalidate()`, a different new URL → one retry; the same URL → no retry.
- [ ] **Step 2: Run, make sure they fail.**
- [ ] **Step 3: Implement.** A module cache (`dict` + `threading.Lock`), TTL from `catalog_cache_ttl` (env `CATALOG_CACHE_TTL`, default 60), fetch `GET {AUTH_API_URL}/internal/catalog` with `internal_headers()` and `timeout=3`.
- [ ] **Step 4: Run the tests** → PASS.
- [ ] **Step 5: Commit** `feat(shared): service catalog client`.

### Task 10: The CLI uses the catalog

**Files:**
- Modify: `cli/cloudx_cli/common.py` (`DEFAULT_URLS` becomes a lazy mapping)
- Test: `tests/cli/test_catalog_urls.py`

**Interfaces:**
- Consumes: the `POST /auth/tokens` response (`token`, `catalog`) or `GET /v3/auth/catalog` (Task 8).
- Produces: `DEFAULT_URLS` can still be indexed as `DEFAULT_URLS["image"]` (about 300 call sites do not change). Order: env `CLOUDX_<SVC>_URL` → the `public` endpoint from the catalog → `http://127.0.0.1:<port>`. The mapping of CLI keys to catalog names in `common.py`: `auth→identity`, `network→network`, `volume→volume`, `file_store→file-store`, the rest the same.

- [ ] **Step 1: Write the tests** (`monkeypatch` `requests`): the catalog from login is used when there is no env; env beats the catalog; `CLOUDX_TOKEN` only → the catalog is fetched through `GET /v3/auth/catalog` with that token; auth without the route (404) or unreachable → the `127.0.0.1` defaults without an exception (Review Focus 5); the result is cached per process (one network call for many indexes); the `auth` key never triggers a catalog fetch.
- [ ] **Step 2: Run, make sure they fail.**
- [ ] **Step 3: Implement** a `dict` subclass with a `__getitem__` that resolves lazily; `auth` always comes from env/default.
- [ ] **Step 4: Run the tests** → PASS; `tests/cli` stays green.
- [ ] **Step 5: Verify in the lab:** copy the latest `services/` to infra01 and restart `cloudx-auth-api`; run `cloudx-manage catalog sync --dry-run`, check the list of changes (`127.0.0.1` URLs become the real node addresses, 9 new services), run without `--dry-run`; from another node `cloudx image list` without `CLOUDX_IMAGE_URL` returns the image list.
- [ ] **Step 6: Commit** `feat(cli): resolve service URLs from the catalog`.

---

## Phase 3: nginx and sockets

### Task 11: Flip the 20 APIs to nginx

**Files:**
- Modify: `packaging/manifest.toml` (20 APIs → `frontend = "nginx"`), `scripts/cloudx_deb/build.py`
- Test: `tests/packaging/test_build.py` (additions)

**Interfaces:**
- Consumes: `render_nginx`, `render_api_unit`.
- Produces: in the nginx API packages: `/etc/nginx/conf.d/cloudx-<name>.conf` (conffile); `Depends: nginx` on the nginx API packages.

- [ ] **Step 1: Write the tests:** the package `cloudx-image-api` contains the snippet and `conffiles` lists it; the package `cloudx-object-store-api` does not; the nginx API `postinst` passes `sh -n` and contains `nginx -t` before the nginx reload; `test_manifest.py::test_frontend_split` (Task 1) already tests the 20/4 split.
- [ ] **Step 2: Run, make sure they fail.**
- [ ] **Step 3: Change the manifest and the builder.** A failing `nginx -t` prints a message but does not fail `dpkg`.
- [ ] **Step 4: Run the tests** → PASS.
- [ ] **Step 5: Verify on a clean node:** install `cloudx-image-api` and `cloudx-auth-api`; `ss -ltn` shows nginx on 8002 and 8005 and gunicorn not listening on TCP; `curl http://<node>:8002/health` 200; `ls -l /run/cloudx/*.sock` gives `srwxrwxrwx` (created by gunicorn; the nginx workers depend on it); uploading a large image (over 1 GB) through `cloudx image create` succeeds (the streaming path); `curl -H 'X-Forwarded-For: 6.6.6.6' .../health` changes nothing in the log; after a reboot both units are active.
- [ ] **Step 6: Commit** `feat(packaging): front the 20 stateless APIs with nginx`.

---

## Phase 4: Meta packages, CLI, documents

### Task 12: Meta packages, `cloudx-cli`, host scripts

**Files:**
- Modify: `scripts/cloudx_deb/build.py`
- Test: `tests/packaging/test_meta_packages.py`

**Interfaces:**
- Produces: the packages `cloudx-controller`, `cloudx-compute`, `cloudx-storage` (no files, `Depends` on `cloudx-<unit>` according to `roles`, `Recommends` for controller: `postgresql, rabbitmq-server, memcached, ovn-central`; for compute: `libvirt-daemon-system, qemu-kvm, openvswitch-switch, ovn-host, qemu-utils`); `cloudx-cli` (its own venv in `/opt/cloudx/cli/venv`, command `/usr/bin/cloudx`); in `cloudx-compute`: `/usr/sbin/cloudx-setup-compute` (from `scripts/setup-compute-node.sh`) and `/usr/sbin/cloudx-setup-ceph-secret` (from `scripts/setup-ceph-libvirt-secret.sh`).

- [ ] **Step 1: Write the tests:** the `Depends` of `cloudx-compute` contains exactly the seven compute-role packages; `cloudx-controller` contains `cloudx-dashboard` and `cloudx-conductor`; `cloudx-storage` contains `cloudx-block-store-worker`; `cloudx-cli` does not `Depends` on `cloudx-common`; both host scripts exist, are executable, and are not in `postinst`.
- [ ] **Step 2: Run, make sure they fail.**
- [ ] **Step 3: Implement** in `build.py`.
- [ ] **Step 4: Run the tests** → PASS.
- [ ] **Step 5: Commit** `feat(packaging): role meta packages, cloudx-cli and host setup scripts`.

### Task 13: Installation document and lab upgrade

**Files:**
- Create: `docs/deb-install.md`
- Modify: `docs/README.md` (link)

- [ ] **Step 1: Write `docs/deb-install.md`** (in Indonesian when this plan was written; the document is English now), in the style of the other documents: building (`make deb`), a local repo (`dpkg-scanpackages . | gzip > Packages.gz`), installing by role, the order (`apt install cloudx-controller`, fill in `cloudx.conf`, `cloudx-manage db sync`, `cloudx-manage catalog sync`, restart), the lab node upgrade runbook from the tarball: `systemctl disable --now` every `cloudx-*` unit, delete `/etc/systemd/system/cloudx-*.service`, `apt install -o Dpkg::Options::=--force-confold ./cloudx-*.deb`, restart nginx. Name the limitations in the spec (four `direct` services, TLS and path routing out of scope).
- [ ] **Step 2: Verify:** run the runbook on one lab node (infra03) and check that 10 units are `active` again, `/health` 200 on all ports, `cloudx server list` still works.
- [ ] **Step 3: Commit** `docs: deb install guide and lab upgrade runbook`.

---

## Phase 5: Catalog adoption

Rules for Tasks 14-16: every read of `<NAME>_API_URL` (except `AUTH_API_URL`) is replaced by `shared.catalog.url("<catalog name>")`, evaluated at the time of use, not at import. Env overrides keep working because `catalog.url` checks them first, so tests that fill the env still pass; tests that `monkeypatch` a module constant are changed to fill the env or patch `catalog.url`. In `compute_worker/app/config.py:endpoint` the order becomes worker env, catalog, message argument, default.

### Task 14: API services

**Files (modify; the tests that touch them are updated too):** `block_store/app/{config.py,routers/volumes.py,service.py}`, `compute/app/{clients.py,config.py,serializers.py,service.py,routers/ops.py,routers/server_actions.py,routers/server_ops.py,routers/servers.py}`, `container/app/main.py`, `database/app/provision.py`, `dns/app/main.py`, `file_store/app/main.py`, `ha/app/monitor.py`, `load_balancer/app/main.py`, `metadata/app/{clients.py,config.py}`, `networking/app/{quota.py,reconciler.py}`, `orchestration/app/engine.py`, `reservation/app/main.py`, `billing/app/main.py`, `dashboard/app/main.py` (all under `services/`).

- [ ] **Step 1: Write the tests first:** for `compute/app/clients.py` and `networking/app/quota.py`, one test per file: with the env `<NAME>_API_URL` empty and `shared.catalog.url` patched to return `http://catalog-host:PORT`, the (patched) HTTP call goes to that host. Run, make sure they fail.
- [ ] **Step 2: Replace the reads** following the rules; run `pytest tests/compute tests/networking tests/block_store tests/load_balancer tests/database tests/dns tests/container tests/file_store tests/ha tests/metadata tests/orchestration tests/reservation tests/billing -q` → PASS (fix the tests that patch constants).
- [ ] **Step 3: Commit** `refactor: API services resolve peers through the catalog`.

### Task 15: Workers

**Files (modify):** `{bgp_worker,billing_worker,container_worker,dns_worker,event_worker,file_store_worker,image_worker,k8s_engine_worker,lb_worker,orchestration_worker,telemetry_worker,vpn_worker}/app/main.py`, `image_worker/app/reconciler.py`.

- [ ] **Step 1: Write the tests first:** one test in `tests/image/` for `image_worker/app/reconciler.py` with the pattern of Task 14. Make sure it fails.
- [ ] **Step 2: Replace the reads** following the rules; `pytest tests/bgp_worker tests/billing tests/container tests/dns_worker tests/event tests/file_store tests/image tests/k8s_engine_worker tests/orchestration tests/telemetry tests/vpn_worker -q` → PASS.
- [ ] **Step 3: Commit** `refactor: workers resolve peers through the catalog`.

### Task 16: Host daemons

**Files (modify):** `block_store_worker/app/main.py`, `compute_worker/app/{config.py,image_manager.py,main.py,worker.py}`, `metadata_proxy/app/main.py`, `ha_watchdog/app/main.py`, `reaper/app/main.py`.

- [ ] **Step 1: Write the tests first:** `test_endpoint_prefers_worker_env_then_catalog_then_message` for `compute_worker/app/config.py:endpoint` (the order in the phase rule). Make sure it fails.
- [ ] **Step 2: Replace the reads** following the rules; `pytest tests/compute_worker tests/block_store_worker tests/metadata tests/ha_watchdog tests/reaper -q` → PASS.
- [ ] **Step 3: Commit** `refactor: host daemons resolve peers through the catalog`.

### Task 17: Guard test, default conf, and lab

**Files:**
- Create: `tests/shared/test_no_direct_service_urls.py`
- Modify: `etc/cloudx/cloudx.conf.sample`, `scripts/setup-compute-node.sh`

- [ ] **Step 1: Write the test** that scans `services/**/*.py` (except `tests/`, `migrations/`, `shared/catalog.py`, `shared/services.py`, `auth/app/config.py`) and fails for every `os.getenv("X_API_URL"...)`/`os.environ["X_API_URL"]` with `X` other than `AUTH`; `etc/cloudx/cloudx.conf.sample` contains only `auth_api_url` among the active `*_api_url` keys (the other keys are commented out as overrides). Run it; it fails if anything was missed in Tasks 14-16.
- [ ] **Step 2: Fix** the remaining direct reads; change the conf sample and the URL block in `scripts/setup-compute-node.sh` (only `auth_api_url`, `rabbitmq_url`, `database_url` are written). Run the tests → PASS; `pytest -q`.
- [ ] **Step 3: Verify in the lab:** remove the `*_api_url` lines other than `auth_api_url` from `cloudx.conf` on one test node (not all at once), restart its services, boot a VM from an RBD volume through `cloudx server create`; repeat per node after it passes.
- [ ] **Step 4: Commit** `refactor: only auth_api_url remains in the shipped config`.

---

## Self-Review (spec to tasks)
- Manifest: Task 1. Layout and packages: Tasks 5, 12. Launcher and the `--forwarded-allow-ips` flag: Task 2. nginx and 20/4: Tasks 4, 11. Daemons: Tasks 2, 4. Catalog (table, sync, client, CLI, adoption, guard test): Tasks 6-10, 14-17. `cloudx-manage` and the alias: Task 3. Build: Task 5. Document and upgrade runbook: Task 13. Client-IP security: Global Constraints, Task 2 (the XFF integration test), Task 11 (lab verification).
- The trusted-proxy follow-up (the four `direct` services) deliberately has no task: the spec calls it a separate spec.
- Types are used consistently: `Unit` (Task 1) in Tasks 4/5/11/12; `CatalogService`, `by_name`, `public_key` (Task 6) in Tasks 7/9; `sync_catalog` (Task 7) in the bootstrap; `url`, `invalidate`, `request` (Task 9) in Tasks 14-16.
