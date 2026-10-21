# Paket .deb dan runtime CloudX ala OpenStack: Rencana Implementasi

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Operator memasang CloudX dengan `apt install cloudx-<unit>`, menjalankan tiap service lewat perintah sendiri di belakang nginx, dan service saling menemukan lewat service catalog.

**Architecture:** Satu manifest (`packaging/manifest.toml`) menghasilkan paket `.deb`, unit systemd, wrapper `/usr/bin/cloudx-<unit>`, dan snippet nginx. Wrapper memanggil `shared/launcher.py` (gunicorn + UvicornWorker; unix socket untuk API di belakang nginx). Catalog auth diisi lewat `cloudx-manage catalog sync` dan dibaca lewat `shared/catalog.py`; URL di conf tetap menjadi override.

**Tech Stack:** Python 3.12, FastAPI, gunicorn, nginx, `dpkg-deb`/`fakeroot`, pytest, Ubuntu 24.04 amd64.

**Spec:** `docs/superpowers/specs/2026-10-21-openstack-style-packaging-design.md`. Baca spec dulu; plan hanya memuat keputusan yang tidak ada di sana.

## Global Constraints
- Target: Ubuntu 24.04, amd64 saja. `Depends: python3 (>= 3.12), python3 (<< 3.13)`.
- Lokasi tetap `/opt/cloudx/{venv,services,cli}`. Port luar tidak berubah. Nama paket Python `app` tidak diubah.
- Versi paket = isi `VERSION` + `-1`. `etc/systemd/`, `scripts/install-systemd.sh`, dan `scripts/build-tarball.sh` tidak diubah.
- `frontend=nginx` untuk 20 API (auth, billing, block-store, compute, container, database, dns, event, file-store, ha, image, k8s-engine, key-manager, load-balancer, messaging, networking, orchestration, placement, reservation, telemetry). `frontend=direct` untuk object-store, metadata-api, metadata-proxy, dashboard.
- `--forwarded-allow-ips` tidak pernah `*`; untuk `frontend=nginx` nilainya kosong. nginx memakai `proxy_set_header X-Forwarded-For $remote_addr` (menimpa).
- `workers`: `<unit>.conf` > `cloudx.conf` > nilai manifest. API nginx: `User=` dari manifest, `Group=cloudx`, `UMask=0007`, `Restart=always`, `RestartSec=5s`.
- Resolusi URL service: (1) kunci `<url_key>` di conf/env, (2) catalog dari auth (`GET /internal/catalog`, cache `catalog_cache_ttl`, default 60 s), (3) `CatalogError` yang menyebut nama service. `auth_api_url` selalu dari conf.
- `cloudx-db-migrate` tetap ada sebagai alias `cloudx-manage db sync`.
- Setiap task diakhiri `pytest -q` hijau dan `ruff check services cli tests --select F401,F811,F821 --exclude "node_modules,migrations/versions"` bersih. Commit diakhiri baris `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus
1. Auth/catalog tidak terjangkau saat URL diresolusi: pakai cache lama bila ada, bila tidak `CatalogError` yang menyebut nama service (Task 9).
2. `workers` berisi `0`, `-1`, `abc`, atau kosong: launcher berhenti dengan pesan yang menyebut unit dan kunci, bukan melempar traceback gunicorn (Task 2).
3. Pasang ulang atau upgrade paket tidak boleh membangkitkan ulang secret yang sudah ada (`auth_secret_key`, `internal_token`, dan lainnya): token semua pengguna akan batal (Task 3, dipakai postinst di Task 5).
4. `ha_api_url` berisi beberapa URL dipisah koma: `catalog sync` mendaftarkan yang pertama, dan override dikembalikan apa adanya (Task 7 dan 9).
5. CLI memakai `CLOUDX_TOKEN` saja, atau auth lama tanpa rute catalog: tetap jalan dengan env/default, tanpa crash (Task 10).

---

## Fase 1: Fondasi

### Task 1: Manifest dan pemuatnya

**Files:**
- Create: `packaging/manifest.toml`, `scripts/cloudx_deb/__init__.py`, `scripts/cloudx_deb/manifest.py`, `tests/packaging/test_manifest.py`
- Modify: `pytest.ini` (`pythonpath = . services scripts`)

**Interfaces:**
- Produces: `class ManifestError(ValueError)`; `@dataclass(frozen=True) class Unit` dengan field `name: str, kind: str, dir: str | None, port: int | None, workers: int | None, frontend: str | None, streaming: bool, catalog: str | None, roles: tuple[str, ...], depends: tuple[str, ...], bundle_with: str | None`; properti `Unit.package -> "cloudx-<name>"`, `Unit.unit_file -> "cloudx-<name>.service"`; `load_manifest(path: Path = DEFAULT_MANIFEST) -> list[Unit]`.

- [ ] **Step 1: Tulis tes** (`tests/packaging/test_manifest.py`):
  - `test_every_systemd_unit_has_one_manifest_entry`: himpunan `cloudx-<name>.service` dari manifest == `etc/systemd/*.service`.
  - `test_api_fields_match_the_current_unit`: untuk tiap `kind=api`, `dir` == basename `WorkingDirectory=` unit, `port` == angka di `--bind 0.0.0.0:<port>` atau `--port <port>`, `workers` == angka `-w` (dashboard, tanpa `-w`: 1).
  - `test_names_and_ports_are_unique`.
  - `test_frontend_split`: `{u.name for u in units if u.frontend == "nginx"}` sama dengan 20 nama di Global Constraints (akhiran `-api` dihapus dari nama manifest, mis. `image-api`); `direct` sama dengan empat nama.
  - `test_roles`: `controller`, `compute`, `storage` berisi tepat daftar di Step 3.
  - `test_bad_manifest_raises`: dua entri bernama sama, `frontend` selain `nginx|direct`, `kind` selain `api|daemon|unit` memicu `ManifestError`.
- [ ] **Step 2: Jalankan, pastikan gagal:** `pytest tests/packaging/test_manifest.py -q` → error import.
- [ ] **Step 3: Tulis `packaging/manifest.toml` dan `manifest.py`.** Satu `[[unit]]` per file di `etc/systemd/`; `name` = nama unit tanpa `cloudx-`. Semua `frontend = "direct"` di fase ini (dibalik di Task 11), kecuali yang jelas sudah `direct` (4 service). `streaming = true` hanya `image-api`. `catalog` = nama catalog (Task 6) untuk API yang punya; metadata-proxy dan dashboard tanpa. `bundle_with = "dns-worker"` untuk `named-auth` dan `named-resolver` (kind `unit`); `depends = ["bind9"]` di `dns-worker`. Peran: `controller` = semua `*-api` (termasuk dashboard), `conductor`, `reaper`, `image-worker`, `billing-worker`, `event-worker`, `orchestration-worker`, `k8s-engine-worker`, `dns-worker`; `storage` = `block-store-worker`; `compute` = `compute-worker`, `metadata-proxy`, `telemetry-worker`, `lb-worker`, `vpn-worker`, `bgp-worker`, `ha-watchdog`. `container-worker` dan `file-store-worker` tanpa peran (butuh host dikonfigurasi). Parse dengan `tomllib`, validasi di `load_manifest`.
- [ ] **Step 4: Jalankan tes** → PASS. Lalu `pytest -q`.
- [ ] **Step 5: Commit** `feat(packaging): manifest of CloudX units`.

### Task 2: Launcher

**Files:**
- Create: `services/shared/launcher.py`, `tests/shared/test_launcher.py`

**Interfaces:**
- Consumes: `shared.config.load_service_config(name) -> dict`.
- Produces: `resolve_workers(unit: str, default: int, config: Mapping[str, str]) -> int` (raise `ValueError` berpesan `"<unit>: workers must be a positive integer, got '<nilai>'"`); `gunicorn_argv(app_dir: str, bind: str, workers: int, frontend: str) -> list[str]`; `run_api(name: str, app_dir: str, port: int, workers: int, frontend: str) -> NoReturn`; `run_daemon(name: str, app_dir: str) -> NoReturn`.

- [ ] **Step 1: Tulis tes** (`tests/shared/test_launcher.py`):
  - `test_workers_precedence`: config `{"workers": "3"}` menang atas default 2; tanpa kunci → 2.
  - `test_bad_workers_values` (parametrize `"0"`, `"-1"`, `"abc"`, `""`): `ValueError` dengan teks `workers must be a positive integer`.
  - `test_nginx_binds_a_unix_socket_and_trusts_no_forwarded_ips`: `gunicorn_argv("/opt/cloudx/services/image", "unix:/run/cloudx/image-api.sock", 2, "nginx")` berisi `-k uvicorn.workers.UvicornWorker`, `-w 2`, `--bind unix:...`, `--chdir`, `app.main:app` sebagai elemen terakhir, `--forwarded-allow-ips=` dan tidak berisi `*`.
  - `test_direct_binds_tcp_and_never_passes_a_wildcard_for_forwarded_ips`.
  - `test_run_daemon_changes_directory_and_runs_app_main`: `monkeypatch` `os.chdir` dan `runpy.run_module`; panggilan `("app.main", run_name="__main__")` setelah chdir.
  - `test_client_supplied_x_forwarded_for_is_ignored_on_a_unix_socket` (integrasi, skip bila `gunicorn` atau `curl` tidak ada): jalankan aplikasi ASGI kecil di `tmp_path` memakai argv dari `gunicorn_argv(..., "nginx")`, `curl --unix-socket ... -H 'X-Forwarded-For: 6.6.6.6'`; aplikasi mengembalikan `scope["client"]`; assert hasilnya tidak berisi `6.6.6.6`.
- [ ] **Step 2: Jalankan, pastikan gagal.** `pytest tests/shared/test_launcher.py -q`.
- [ ] **Step 3: Implementasi.** `run_api` memuat `load_service_config(name)`, `resolve_workers`, memilih bind (`unix:/run/cloudx/<name>.sock` untuk `nginx`, `0.0.0.0:<port>` untuk `direct`), lalu `os.execv` ke gunicorn di direktori yang sama dengan `sys.executable`. ValueError dari `resolve_workers` dicetak ke stderr dan keluar `2`. `run_daemon`: `load_service_config(name)`, `os.chdir(app_dir)`, `sys.path.insert(0, app_dir)`, `runpy.run_module("app.main", run_name="__main__")`.
- [ ] **Step 4: Jalankan tes** → PASS (tes integrasi bila gunicorn ada di venv).
- [ ] **Step 5: Commit** `feat(shared): launcher for CloudX APIs and daemons`.

### Task 3: `cloudx-manage` (db sync, conf init)

**Files:**
- Create: `services/shared/manage.py`, `tests/shared/test_manage.py`
- Modify: `services/pyproject.toml` (`[project.scripts]`: `cloudx-manage = "shared.manage:main"`; `cloudx-db-migrate` tetap)

**Interfaces:**
- Consumes: `shared.migrate.main(argv: list[str] | None = None) -> int`.
- Produces: `main(argv: list[str] | None = None) -> int` dengan subperintah `db sync [service ...]`, `conf init [--path PATH] [--sample PATH]`; `init_conf(path: Path, sample: Path) -> list[str]` (daftar kunci yang dibangkitkan).

- [ ] **Step 1: Tulis tes:**
  - `test_db_sync_delegates_to_migrate`: `monkeypatch` `shared.migrate.main`; `main(["db","sync","compute"])` memanggilnya dengan `["compute"]`.
  - `test_conf_init_creates_missing_conf_with_generated_secrets`: file belum ada → dibuat dari sample, mode `0o640`, kelima kunci (`auth_secret_key`, `auth_admin_password`, `key_manager_master_key`, `metrics_token`, `internal_token`) terisi.
  - `test_conf_init_never_regenerates_existing_secrets` (Review Focus 3): jalankan dua kali; isi `auth_secret_key` identik, kembalian kedua `[]`; kunci kosong yang ditambahkan belakangan terisi tanpa menyentuh yang lain.
- [ ] **Step 2: Jalankan, pastikan gagal.**
- [ ] **Step 3: Implementasi.** Logika `init_conf` mengikuti fungsi `gen()` di `scripts/install-systemd.sh:87-96` (hex 32 byte untuk `auth_secret_key` dan `key_manager_master_key`, 24 untuk token, base64 18 karakter tanpa `/+=` untuk password admin) memakai `secrets`; baca/tulis tanpa `configparser` merusak komentar (ganti baris, sisipkan di bawah `[DEFAULT]`).
- [ ] **Step 4: Jalankan tes** → PASS.
- [ ] **Step 5: Commit** `feat(shared): cloudx-manage with db sync and conf init`.

### Task 4: Renderer unit, wrapper, snippet nginx

**Files:**
- Create: `scripts/cloudx_deb/render.py`, `tests/packaging/test_render.py`, `tests/packaging/golden/` (file golden)

**Interfaces:**
- Consumes: `Unit` (Task 1).
- Produces: `render_wrapper(u: Unit) -> str` (skrip `#!/opt/cloudx/venv/bin/python` yang memanggil `run_api(...)` atau `run_daemon(...)`); `render_api_unit(u: Unit) -> str`; `render_daemon_unit(u: Unit, source: str) -> str` (salin `source` dari `etc/systemd/`, ganti hanya baris `ExecStart=` menjadi `/usr/bin/cloudx-<name>`); `render_nginx(u: Unit) -> str`.

- [ ] **Step 1: Tulis tes** dengan golden untuk `image-api` (nginx, streaming), `auth-api` (nginx), `object-store-api` (direct), `compute-worker` (daemon):
  - Unit API nginx berisi `Group=cloudx`, `UMask=0007`, `Restart=always`, `RestartSec=5s`, `ExecStart=/usr/bin/cloudx-auth-api`; unit untuk user root (`block-store-api`) tetap `User=root` dengan `Group=cloudx`.
  - Snippet nginx: `listen 8002;`, `proxy_pass http://unix:/run/cloudx/image-api.sock`, `proxy_set_header X-Forwarded-For $remote_addr;` (dan tidak ada `$proxy_add_x_forwarded_for`), `proxy_http_version 1.1`; hanya image-api memuat `client_max_body_size 0`, `proxy_request_buffering off`, `proxy_read_timeout 3600`.
  - `render_nginx` untuk unit `direct` melempar `ValueError` (tidak ada snippet).
  - Unit daemon identik dengan sumbernya kecuali `ExecStart=`.
- [ ] **Step 2: Jalankan, pastikan gagal.**
- [ ] **Step 3: Implementasi** empat fungsi. Perbarui golden secara sadar (tes membandingkan; skrip kecil `--update-golden` tidak diperlukan, tulis golden dari keluaran pertama yang sudah diperiksa mata).
- [ ] **Step 4: Jalankan tes** → PASS.
- [ ] **Step 5: Commit** `feat(packaging): render units, wrappers and nginx snippets`.

### Task 5: Pembangun paket

**Files:**
- Create: `scripts/cloudx_deb/build.py`, `scripts/build-deb.sh`, `tests/packaging/test_build.py`
- Modify: `Makefile` (target `deb`), `.gitignore` (`dist/deb/`, `build/deb/` bila belum tercakup)

**Interfaces:**
- Consumes: `load_manifest`, `render_*`.
- Produces: `build_all(out_dir: Path, version: str, *, with_venv: bool = True, only: list[str] | None = None) -> list[Path]` (hasil `.deb`); `control_file(pkg: str, version: str, depends: list[str], description: str) -> str`.

- [ ] **Step 1: Tulis tes** (skip bila `dpkg-deb` tidak ada; pakai `with_venv=False`, `only=["common","db","image-api","compute-worker"]`):
  - `test_packages_have_the_expected_names_and_versions`: `dpkg-deb --field` memberi `Package: cloudx-image-api`, `Version: <VERSION>-1`, `Architecture: amd64`.
  - `test_unit_package_contents`: `dpkg-deb --contents` memuat `/lib/systemd/system/cloudx-image-api.service`, `/usr/bin/cloudx-image-api`, `/opt/cloudx/services/image/app/main.py`, `/etc/cloudx/image-api/image-api.conf`; tidak ada `/etc/systemd/system/`, tidak ada `__pycache__`, tidak ada `tests/`, tidak ada `migrations/`.
  - `test_conffiles_are_declared`: `conffiles` memuat `/etc/cloudx/image-api/image-api.conf` (nginx snippet masuk setelah Task 11).
  - `test_depends`: `cloudx-image-api` `Depends: cloudx-common (= <VERSION>-1)`; `cloudx-common` memuat `python3 (>= 3.12), python3 (<< 3.13)`; `cloudx-dns-worker` memuat `bind9`.
  - `test_db_package_owns_all_migrations_and_units_do_not`: `cloudx-db` memuat `/opt/cloudx/services/migrations/registry.py` dan `/opt/cloudx/services/image/migrations/`; tidak ada file yang dimiliki dua paket.
  - `test_postinst_enables_and_starts_via_systemd_helpers`: skrip `postinst` unit lolos `sh -n`, memuat `deb-systemd-helper` dan `deb-systemd-invoke`; `postinst` `cloudx-common` memanggil `cloudx-manage conf init`.
- [ ] **Step 2: Jalankan, pastikan gagal.**
- [ ] **Step 3: Implementasi.** `cloudx-common`: venv dibangun di `<staging>/opt/cloudx/venv` dari `requirements.txt` plus `pip install --no-deps services/` (seperti `install-systemd.sh:167-170`), lalu tulis ulang prefix staging ke `/opt/cloudx/venv` di `bin/*` dan `pyvenv.cfg`; contoh `etc/cloudx/cloudx.conf.sample` ke `/usr/share/cloudx/`; `tmpfiles.d` untuk `/run/cloudx` (`0755 cloudx cloudx`); `postinst`: buat user `cloudx`, `/var/lib/cloudx`, `cloudx-manage conf init --path /etc/cloudx/cloudx.conf --sample /usr/share/cloudx/cloudx.conf.sample`. Paket unit: salin `services/<dir>` tanpa `migrations/`, `tests/`, `__pycache__`, `frontend/`, `node_modules`; conf dari `etc/cloudx/<name>/<name>.conf.sample` bila ada, jika tidak `[DEFAULT]`. `cloudx-db`: `services/migrations/` dan `services/*/migrations/`. Bangun dengan `fakeroot dpkg-deb --build`. `scripts/build-deb.sh` memanggil `python3 -c "from cloudx_deb.build import build_all..."` dengan `VERSION`; `make deb`.
- [ ] **Step 4: Jalankan tes** → PASS. Lalu `pytest -q`.
- [ ] **Step 5: Verifikasi di node bersih** (node atau VM Ubuntu 24.04 tanpa `/opt/cloudx`; bila tidak ada, unggah image Ubuntu 24.04 cloud ke lab dan boot VM): `make deb`, salin hasil, `apt install ./cloudx-common_*.deb ./cloudx-db_*.deb ./cloudx-image-api_*.deb`; hasil yang diharapkan: `/opt/cloudx/venv/bin/python -c "import shared"` sukses, `systemctl is-active cloudx-image-api` `active` (butuh PostgreSQL: arahkan `database_url` ke lab, jalankan `cloudx-manage db sync`), `curl http://127.0.0.1:8002/health` 200 (frontend masih `direct`).
- [ ] **Step 6: Commit** `feat(packaging): build .deb packages from the manifest`.

---

## Fase 2: Catalog

### Task 6: Tabel catalog tunggal

**Files:**
- Create: `services/shared/services.py`, `tests/shared/test_services_table.py`
- Modify: `services/auth/app/config.py` (hapus `_CLOUDX_SERVICES` literal, ganti dengan `build_catalog_services()`)

**Interfaces:**
- Produces: `@dataclass(frozen=True) class CatalogService: name, type, url_key, port`; `SERVICES: tuple[CatalogService, ...]` (22 baris: identity/identity/`auth_api_url`/8005; compute/compute/`compute_api_url`/8003; image/image/`image_api_url`/8002; network/network/`networking_api_url`/8001; volume/block-storage/`block_store_api_url`/8004; placement/placement/`placement_api_url`/8006; object-store/object-store/`object_store_api_url`/8007; load-balancer/load-balancer/`load_balancer_api_url`/8008; metadata/metadata/`metadata_api_url`/8775; telemetry/telemetry/`telemetry_api_url`/8010; billing/billing/`billing_api_url`/8011; event/event/`event_api_url`/8012; orchestration/orchestration/`orchestration_api_url`/8013; key-manager/key-manager/`key_manager_api_url`/8014; k8s-engine/k8s-engine/`k8s_engine_api_url`/8015; dns/dns/`dns_api_url`/8016; file-store/file-store/`file_store_api_url`/8017; container/container/`container_api_url`/8018; database/database/`database_api_url`/8019; ha/ha/`ha_api_url`/8020; messaging/messaging/`messaging_api_url`/8022; reservation/reservation/`reservation_api_url`/8023); `by_name(name: str) -> CatalogService` (`KeyError` bila tidak ada); `public_key(url_key: str) -> str` (`image_api_url` → `image_api_public_url`).

- [ ] **Step 1: Tulis tes:** `test_table_matches_manifest` (tiap `Unit.catalog` ada di tabel, dan `port` sama dengan `CatalogService.port`; tiap entri tabel punya satu unit manifest `api`); `test_names_unique`; `test_public_key`; `test_auth_bootstrap_registers_every_service` (`auth.app.config._CLOUDX_SERVICES` memuat semua `name` tabel dengan `url` dari env `url_key.upper()` atau `http://127.0.0.1:<port>`).
- [ ] **Step 2: Jalankan, pastikan gagal.**
- [ ] **Step 3: Implementasi** tabel dan `_CLOUDX_SERVICES` dibangun darinya (default `http://127.0.0.1:<port>` menggantikan `http://<nama>-api:8000`).
- [ ] **Step 4: Jalankan tes** → PASS; tes auth yang ada tetap hijau.
- [ ] **Step 5: Commit** `feat(auth): one service table for the catalog`.

### Task 7: `catalog sync`

**Files:**
- Create: `services/auth/app/catalog_sync.py`, `tests/auth/test_catalog_sync.py`
- Modify: `services/shared/manage.py` (subperintah `catalog sync [--dry-run]`), `services/auth/app/bootstrap.py` (`_bootstrap_service_catalog` memakai `sync_catalog(..., create_only=True)`)

**Interfaces:**
- Consumes: `shared.services.SERVICES`, `public_key`; `auth.app.models.ServiceCatalogEntry`, `ServiceEndpoint`.
- Produces: `sync_catalog(db: Session, urls: Mapping[str, str], *, dry_run: bool = False, create_only: bool = False) -> list[str]` (satu baris per perubahan, mis. `+ image public http://...`, `~ image internal http://old -> http://new`); `urls` dipetakan dari `url_key` (lowercase) ke URL.

- [ ] **Step 1: Tulis tes** (SQLite in-memory seperti `tests/auth/` yang ada):
  - `test_adds_missing_services_with_public_and_internal_endpoints`.
  - `test_updates_changed_urls_and_reports_each_change`.
  - `test_second_run_is_a_no_op` (kembalian `[]`).
  - `test_keys_missing_from_conf_are_left_alone` (tidak menghapus apa pun).
  - `test_public_override_key`: `image_api_public_url` mengisi endpoint `public` saja.
  - `test_comma_list_registers_the_first_url` (Review Focus 4): `ha_api_url = http://a:8020,http://b:8020` → `http://a:8020`.
  - `test_dry_run_changes_nothing`; `test_create_only_never_updates_existing`.
- [ ] **Step 2: Jalankan, pastikan gagal.**
- [ ] **Step 3: Implementasi** `sync_catalog`; `catalog sync` di `manage.py` memuat `load_service_config("auth-api")`, menaruh direktori `services/` di `sys.path` (cara `shared/migrate.py`), membuka `auth.app.db.SessionLocal`, mencetak baris perubahan; `--dry-run` tidak commit.
- [ ] **Step 4: Jalankan tes** → PASS.
- [ ] **Step 5: Commit** `feat(auth): cloudx-manage catalog sync`.

### Task 8: Rute catalog di auth

**Files:**
- Modify: `services/auth/app/routers/catalog.py`
- Test: `tests/auth/test_catalog_routes.py`

**Interfaces:**
- Consumes: `auth.app.tokens._build_catalog(db) -> list`; `shared.auth_middleware.require_internal`.
- Produces: `GET /v3/auth/catalog` (pengguna login) → `{"catalog": [...]}`; `GET /internal/catalog` (`X-Internal-Token` atau admin) → `{"catalog": [...]}`.

- [ ] **Step 1: Tulis tes** (pola `TestClient` dari tes auth lain): kedua rute mengembalikan entri `image` dengan endpoint `internal`; `/v3/auth/catalog` tanpa token → 401; `/internal/catalog` dengan `INTERNAL_TOKEN` salah → 401/403 (bila token diset), dengan token benar → 200.
- [ ] **Step 2: Jalankan, pastikan gagal.** 
- [ ] **Step 3: Implementasi** dua rute memakai `_build_catalog`.
- [ ] **Step 4: Jalankan tes** → PASS.
- [ ] **Step 5: Commit** `feat(auth): catalog routes for users and services`.

### Task 9: Klien catalog `shared/catalog.py`

**Files:**
- Create: `services/shared/catalog.py`, `tests/shared/test_catalog_client.py`

**Interfaces:**
- Consumes: `shared.services.by_name`; `shared.internal.internal_headers() -> dict`.
- Produces: `class CatalogError(RuntimeError)`; `url(service: str, interface: str = "internal") -> str`; `invalidate() -> None`; `request(method: str, service: str, path: str, **kwargs) -> requests.Response`.

- [ ] **Step 1: Tulis tes** (`monkeypatch` `requests.get`/`requests.request` dan `time.monotonic`):
  - `test_override_wins`: `IMAGE_API_URL` di env → dikembalikan apa adanya, tanpa panggilan jaringan.
  - `test_ha_override_with_several_urls_is_returned_verbatim` (Review Focus 4).
  - `test_resolves_from_the_catalog_and_caches_for_the_ttl`: satu panggilan HTTP untuk dua `url()`; setelah TTL, panggilan kedua.
  - `test_unreachable_auth_serves_the_stale_cache` dan `test_unreachable_auth_without_cache_raises_catalog_error_naming_the_service` (Review Focus 1).
  - `test_unknown_service_or_interface_raises_catalog_error`.
  - `test_request_retries_once_when_the_catalog_url_changed`: `ConnectionError` pada URL lama → `invalidate()`, URL baru berbeda → satu percobaan ulang; URL sama → tidak mengulang.
- [ ] **Step 2: Jalankan, pastikan gagal.**
- [ ] **Step 3: Implementasi.** Cache modul (`dict` + `threading.Lock`), TTL dari `catalog_cache_ttl` (env `CATALOG_CACHE_TTL`, default 60), fetch `GET {AUTH_API_URL}/internal/catalog` dengan `internal_headers()` dan `timeout=3`.
- [ ] **Step 4: Jalankan tes** → PASS.
- [ ] **Step 5: Commit** `feat(shared): service catalog client`.

### Task 10: CLI memakai catalog

**Files:**
- Modify: `cli/cloudx_cli/common.py` (`DEFAULT_URLS` menjadi pemetaan malas)
- Test: `tests/cli/test_catalog_urls.py`

**Interfaces:**
- Consumes: respons `POST /auth/tokens` (`token`, `catalog`) atau `GET /v3/auth/catalog` (Task 8).
- Produces: `DEFAULT_URLS` tetap dapat diindeks `DEFAULT_URLS["image"]` (≈300 pemanggilan tidak berubah). Urutan: env `CLOUDX_<SVC>_URL` → endpoint `public` dari catalog → `http://127.0.0.1:<port>`. Pemetaan kunci CLI ke nama catalog di `common.py`: `auth→identity`, `network→network`, `volume→volume`, `file_store→file-store`, sisanya sama.

- [ ] **Step 1: Tulis tes** (`monkeypatch` `requests`): catalog dari login dipakai bila env tidak ada; env menang atas catalog; `CLOUDX_TOKEN` saja → catalog diambil lewat `GET /v3/auth/catalog` dengan token itu; auth tanpa rute (404) atau tidak terjangkau → default `127.0.0.1` tanpa exception (Review Focus 5); hasil di-cache per proses (satu panggilan jaringan untuk banyak indeks); kunci `auth` tidak pernah memicu pengambilan catalog.
- [ ] **Step 2: Jalankan, pastikan gagal.**
- [ ] **Step 3: Implementasi** subclass `dict` dengan `__getitem__` yang menyelesaikan malas; `auth` selalu dari env/default.
- [ ] **Step 4: Jalankan tes** → PASS; `tests/cli` tetap hijau.
- [ ] **Step 5: Verifikasi di lab:** salin `services/` terbaru ke infra01 dan restart `cloudx-auth-api`; jalankan `cloudx-manage catalog sync --dry-run`, periksa daftar perubahan (URL `127.0.0.1` menjadi alamat node sebenarnya, 9 service baru), jalankan tanpa `--dry-run`; dari node lain `cloudx image list` tanpa `CLOUDX_IMAGE_URL` mengembalikan daftar image.
- [ ] **Step 6: Commit** `feat(cli): resolve service URLs from the catalog`.

---

## Fase 3: nginx dan socket

### Task 11: Membalik 20 API ke nginx

**Files:**
- Modify: `packaging/manifest.toml` (20 API → `frontend = "nginx"`), `scripts/cloudx_deb/build.py`
- Test: `tests/packaging/test_build.py` (tambahan)

**Interfaces:**
- Consumes: `render_nginx`, `render_api_unit`.
- Produces: pada paket API nginx: `/etc/nginx/conf.d/cloudx-<name>.conf` (conffile); pada `cloudx-common`: `/etc/systemd/system/nginx.service.d/cloudx.conf` berisi `[Service]` / `SupplementaryGroups=cloudx`; `Depends: nginx` pada paket API nginx.

- [ ] **Step 1: Tulis tes:** paket `cloudx-image-api` memuat snippet dan `conffiles` mencantumkannya; paket `cloudx-object-store-api` tidak; `cloudx-common` memuat drop-in; `postinst` paket API nginx lolos `sh -n` dan memuat `nginx -t` sebelum `systemctl reload nginx`, dengan restart nginx sekali memakai penanda `/var/lib/cloudx/.nginx-group-applied`; `test_manifest.py::test_frontend_split` (Task 1) sudah menjadi tes untuk pembagian 20/4.
- [ ] **Step 2: Jalankan, pastikan gagal.**
- [ ] **Step 3: Ubah manifest dan builder.** Kegagalan `nginx -t` mencetak pesan tetapi tidak menggagalkan `dpkg`.
- [ ] **Step 4: Jalankan tes** → PASS.
- [ ] **Step 5: Verifikasi di node bersih:** pasang `cloudx-image-api` dan `cloudx-auth-api`; `ss -ltn` menunjukkan nginx pada 8002 dan 8005 dan gunicorn tidak mendengarkan TCP; `curl http://<node>:8002/health` 200; `ls -l /run/cloudx/*.sock` memberi `srw-rw---- cloudx cloudx`; unggah image besar (lebih dari 1 GB) lewat `cloudx image create` sukses (jalur streaming); `curl -H 'X-Forwarded-For: 6.6.6.6' .../health` tidak mengubah apa pun di log; reboot, kedua unit aktif.
- [ ] **Step 6: Commit** `feat(packaging): front the 20 stateless APIs with nginx`.

---

## Fase 4: Paket meta, CLI, dokumen

### Task 12: Paket meta, `cloudx-cli`, skrip host

**Files:**
- Modify: `scripts/cloudx_deb/build.py`
- Test: `tests/packaging/test_meta_packages.py`

**Interfaces:**
- Produces: paket `cloudx-controller`, `cloudx-compute`, `cloudx-storage` (tanpa file, `Depends` ke `cloudx-<unit>` sesuai `roles`, `Recommends` untuk controller: `postgresql, rabbitmq-server, memcached, ovn-central`; untuk compute: `libvirt-daemon-system, qemu-kvm, openvswitch-switch, ovn-host, qemu-utils`); `cloudx-cli` (venv sendiri di `/opt/cloudx/cli/venv`, perintah `/usr/bin/cloudx`); di `cloudx-compute`: `/usr/sbin/cloudx-setup-compute` (dari `scripts/setup-compute-node.sh`) dan `/usr/sbin/cloudx-setup-ceph-secret` (dari `scripts/setup-ceph-libvirt-secret.sh`).

- [ ] **Step 1: Tulis tes:** `Depends` `cloudx-compute` memuat tepat tujuh paket peran compute; `cloudx-controller` memuat `cloudx-dashboard` dan `cloudx-conductor`; `cloudx-storage` memuat `cloudx-block-store-worker`; `cloudx-cli` tidak `Depends` ke `cloudx-common`; kedua skrip host ada, executable, tidak ada di `postinst`.
- [ ] **Step 2: Jalankan, pastikan gagal.**
- [ ] **Step 3: Implementasi** di `build.py`.
- [ ] **Step 4: Jalankan tes** → PASS.
- [ ] **Step 5: Commit** `feat(packaging): role meta packages, cloudx-cli and host setup scripts`.

### Task 13: Dokumen pemasangan dan upgrade lab

**Files:**
- Create: `docs/deb-install.md`
- Modify: `docs/README.md` (tautan)

- [ ] **Step 1: Tulis `docs/deb-install.md`** (Bahasa Indonesia, gaya dokumen lain): membangun (`make deb`), repo lokal (`dpkg-scanpackages . | gzip > Packages.gz`), pemasangan per peran, urutan (`apt install cloudx-controller`, isi `cloudx.conf`, `cloudx-manage db sync`, `cloudx-manage catalog sync`, restart), runbook upgrade node lab dari tarball: `systemctl disable --now` semua unit `cloudx-*`, hapus `/etc/systemd/system/cloudx-*.service`, `apt install -o Dpkg::Options::=--force-confold ./cloudx-*.deb`, restart nginx. Sebut batasan di spec (empat service `direct`, TLS dan path routing di luar cakupan).
- [ ] **Step 2: Verifikasi:** jalankan runbook di satu node lab (infra03) dan periksa 10 unit kembali `active`, `/health` 200 di semua port, `cloudx server list` tetap bekerja.
- [ ] **Step 3: Commit** `docs: deb install guide and lab upgrade runbook`.

---

## Fase 5: Adopsi catalog

Aturan untuk Task 14-16: setiap bacaan `<NAME>_API_URL` (kecuali `AUTH_API_URL`) diganti `shared.catalog.url("<nama catalog>")` yang dievaluasi saat dipakai, bukan saat import. Override env tetap berfungsi karena `catalog.url` memeriksanya dulu, jadi tes yang mengisi env tetap lulus; tes yang mem-`monkeypatch` konstanta modul diubah agar mengisi env atau mem-patch `catalog.url`. Pada `compute_worker/app/config.py:endpoint` urutannya menjadi env worker, catalog, argumen pesan, default.

### Task 14: Service API

**Files (modify; tes yang menyentuhnya ikut diperbarui):** `block_store/app/{config.py,routers/volumes.py,service.py}`, `compute/app/{clients.py,config.py,serializers.py,service.py,routers/ops.py,routers/server_actions.py,routers/server_ops.py,routers/servers.py}`, `container/app/main.py`, `database/app/provision.py`, `dns/app/main.py`, `file_store/app/main.py`, `ha/app/monitor.py`, `load_balancer/app/main.py`, `metadata/app/{clients.py,config.py}`, `networking/app/{quota.py,reconciler.py}`, `orchestration/app/engine.py`, `reservation/app/main.py`, `billing/app/main.py`, `dashboard/app/main.py` (semuanya di bawah `services/`).

- [ ] **Step 1: Tulis tes dulu:** untuk `compute/app/clients.py` dan `networking/app/quota.py`, satu tes tiap file: dengan env `<NAME>_API_URL` kosong dan `shared.catalog.url` di-patch mengembalikan `http://catalog-host:PORT`, panggilan HTTP (di-patch) menuju host itu. Jalankan, pastikan gagal.
- [ ] **Step 2: Ganti pembacaan** sesuai aturan; jalankan `pytest tests/compute tests/networking tests/block_store tests/load_balancer tests/database tests/dns tests/container tests/file_store tests/ha tests/metadata tests/orchestration tests/reservation tests/billing -q` → PASS (perbaiki tes yang mem-patch konstanta).
- [ ] **Step 3: Commit** `refactor: API services resolve peers through the catalog`.

### Task 15: Worker

**Files (modify):** `{bgp_worker,billing_worker,container_worker,dns_worker,event_worker,file_store_worker,image_worker,k8s_engine_worker,lb_worker,orchestration_worker,telemetry_worker,vpn_worker}/app/main.py`, `image_worker/app/reconciler.py`.

- [ ] **Step 1: Tulis tes dulu:** satu tes di `tests/image/` untuk `image_worker/app/reconciler.py` dengan pola Task 14. Pastikan gagal.
- [ ] **Step 2: Ganti pembacaan** sesuai aturan; `pytest tests/bgp_worker tests/billing tests/container tests/dns_worker tests/event tests/file_store tests/image tests/k8s_engine_worker tests/orchestration tests/telemetry tests/vpn_worker -q` → PASS.
- [ ] **Step 3: Commit** `refactor: workers resolve peers through the catalog`.

### Task 16: Daemon host

**Files (modify):** `block_store_worker/app/main.py`, `compute_worker/app/{config.py,image_manager.py,main.py,worker.py}`, `metadata_proxy/app/main.py`, `ha_watchdog/app/main.py`, `reaper/app/main.py`.

- [ ] **Step 1: Tulis tes dulu:** `test_endpoint_prefers_worker_env_then_catalog_then_message` untuk `compute_worker/app/config.py:endpoint` (urutan di aturan fase). Pastikan gagal.
- [ ] **Step 2: Ganti pembacaan** sesuai aturan; `pytest tests/compute_worker tests/block_store_worker tests/metadata tests/ha_watchdog tests/reaper -q` → PASS.
- [ ] **Step 3: Commit** `refactor: host daemons resolve peers through the catalog`.

### Task 17: Tes penjaga, conf bawaan, dan lab

**Files:**
- Create: `tests/shared/test_no_direct_service_urls.py`
- Modify: `etc/cloudx/cloudx.conf.sample`, `scripts/setup-compute-node.sh`

- [ ] **Step 1: Tulis tes** yang memindai `services/**/*.py` (kecuali `tests/`, `migrations/`, `shared/catalog.py`, `shared/services.py`, `auth/app/config.py`) dan gagal untuk setiap `os.getenv("X_API_URL"...)`/`os.environ["X_API_URL"]` dengan `X` selain `AUTH`; `etc/cloudx/cloudx.conf.sample` hanya berisi `auth_api_url` di antara kunci `*_api_url` yang aktif (kunci lain dikomentari sebagai override). Jalankan; gagal bila ada yang terlewat di Task 14-16.
- [ ] **Step 2: Perbaiki** sisa pembacaan langsung; ubah conf sample dan blok URL di `scripts/setup-compute-node.sh` (hanya `auth_api_url`, `rabbitmq_url`, `database_url` yang ditulis). Jalankan tes → PASS; `pytest -q`.
- [ ] **Step 3: Verifikasi di lab:** buang baris `*_api_url` selain `auth_api_url` dari `cloudx.conf` di satu node uji (bukan semua sekaligus), restart layanannya, boot VM dari volume RBD lewat `cloudx server create`; ulangi per node setelah lulus.
- [ ] **Step 4: Commit** `refactor: only auth_api_url remains in the shipped config`.

---

## Self-Review (spec ke tugas)
- Manifest: Task 1. Tata letak dan paket: Task 5, 12. Launcher dan flag `--forwarded-allow-ips`: Task 2. nginx dan 20/4: Task 4, 11. Daemon: Task 2, 4. Catalog (tabel, sync, klien, CLI, adopsi, tes penjaga): Task 6-10, 14-17. `cloudx-manage` dan alias: Task 3. Build: Task 5. Dokumen dan runbook upgrade: Task 13. Keamanan IP klien: Global Constraints, Task 2 (tes integrasi XFF), Task 11 (verifikasi lab).
- Tindak lanjut trusted-proxy (empat service `direct`) sengaja tidak punya task: spec menyebutnya spec terpisah.
- Tipe dipakai konsisten: `Unit` (Task 1) di Task 4/5/11/12; `CatalogService`, `by_name`, `public_key` (Task 6) di Task 7/9; `sync_catalog` (Task 7) di bootstrap; `url`, `invalidate`, `request` (Task 9) di Task 14-16.
