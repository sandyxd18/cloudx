# Paket .deb dan runtime CloudX ala OpenStack

## Tujuan
Operator memasang dan menjalankan CloudX seperti OpenStack: satu paket `.deb` per service, satu perintah per service (`cloudx-image-api`, `cloudx-compute-worker`), satu pintu depan (nginx) di tiap node, dan service saling menemukan lewat service catalog. Hasil yang diharapkan: node infra bisa diisi service apa saja lewat `apt install`, tanpa `install-systemd.sh`, tanpa menyunting URL di setiap node.

Yang dipelajari dari OpenStack (source di `/home/sandy/projects/openstack_source_code`, versi master Oktober 2026, ditambah devstack dari opendev):
- API tidak punya server sendiri. Nova (`nova.wsgi.osapi_compute:application`), Glance (`glance.wsgi.api:application`), Keystone dan Placement hanya mengekspos modul WSGI. Perintah `nova-api` dihapus di Nova 33.0.0, dan `glance-api` mandiri menolak jalan. Server WSGI dipilih operator. Dokumen Nova menyebut uWSGI dan gunicorn.
- Susunan referensi (devstack): Apache `ProxyPass` per API, satu proses uwsgi per API di unix socket `/var/run/uwsgi/<nama>.socket`, unit systemd per API. Glance dan Swift dikecualikan karena butuh chunked upload.
- Daemon (`nova-compute`, `nova-conductor`, agent Neutron) adalah console script di `pyproject.toml` yang memanggil `oslo.service`.
- Satu file INI per service (`/etc/nova/nova.conf`), `<svc>-manage db sync` dijalankan operator, service saling menemukan lewat catalog Keystone, paket per service (`apt install nova-api nova-conductor ...`).
- Tidak terbaca: packaging Debian/Ubuntu-nya (repo salsa 404). Bagian paket di sini berdasar panduan instalasi resmi, bukan isi `debian/`.

## Keadaan CloudX sekarang (diperiksa di repo dan lab, 2026-10-21)
- 43 unit di `etc/systemd/`. API: gunicorn dengan `UvicornWorker` pada `0.0.0.0:<port>`; daemon: `python -m app.main`; dua unit BIND (`cloudx-named-*`).
- Semua API adalah paket `app` di `services/<dir>/` (tidak bisa diimpor bersamaan dalam satu venv). Unit memakai `WorkingDirectory`.
- Config: `/etc/cloudx/cloudx.conf` ditambah `/etc/cloudx/<svc>/<svc>.conf` (`shared/config.py`), dimuat ke env. `cloudx-db-migrate` ada (`shared/migrate.py`).
- Auth sudah berbentuk Keystone: `/v3/services`, `/v3/endpoints`, dan `catalog` di respons `/auth/tokens`.
- **Catalog tidak terpakai dan salah.** `auth/app/bootstrap.py` mengisinya sekali dari env saat pertama jalan dan tidak pernah memperbaruinya. Di lab: 13 service, semua `http://127.0.0.1:...`, padahal image dan block-store kini di infra02. Service key-manager, k8s-engine, dns, file-store, container, database, messaging, reservation, dan ha tidak terdaftar.
- 38 modul membaca `*_API_URL` langsung dari env. CLI membaca `CLOUDX_<SVC>_URL` atau default `127.0.0.1:<port>`, tidak membaca catalog.
- Empat service memakai IP klien (`object_store/app/main.py:_classify_source`, `dashboard/app/main.py`, `metadata/app/metadata_service.py`, `metadata_proxy/app/main.py`). Lihat bagian Keamanan.
- Kode di lab sudah di `/opt/cloudx/{venv,services,cli}`, seperti yang akan dipakai paket.

## Keputusan

### 1. Manifest tunggal: `packaging/manifest.toml`
Satu daftar unit menjadi sumber paket, unit, snippet nginx, dan tes konsistensi. Format TOML (`tomllib` ada di Python 3.12). Per unit:

| Kolom | Isi |
|---|---|
| `name` | `image-api`; nama paket `cloudx-image-api`, unit `cloudx-image-api.service`, perintah `/usr/bin/cloudx-image-api` |
| `kind` | `api`, `daemon`, atau `unit` (BIND, disalin apa adanya) |
| `dir` | direktori di `services/` (`image`) |
| `port`, `workers` | hanya `api`; `workers` default = nilai `-w` unit sekarang |
| `frontend` | `nginx` atau `direct` (lihat 4) |
| `streaming` | `true` bila upload/unduh besar (hanya `image-api`) |
| `catalog_name`, `catalog_type`, `url_key` | pendaftaran catalog (`image`, `image`, `image_api_url`) |
| `roles` | `controller`, `compute`, `storage` (membentuk paket meta) |
| `depends` | paket apt tambahan (misalnya `bind9` untuk dns-worker) |

Unit `daemon` dan `unit` tetap memakai file di `etc/systemd/` (After=, user, capability tidak dikarang ulang); generator hanya mengganti `ExecStart` dengan wrapper. Unit `api` dihasilkan dari templat. Tarball dan `install-systemd.sh` tetap berjalan sampai diganti; `etc/systemd/` tidak diubah.

### 2. Tata letak dan paket (target: Ubuntu 24.04, amd64)
Lokasi tetap `/opt/cloudx` (venv, `services/`, `cli/`), sama dengan node lab sekarang sehingga jalur upgrade pendek.

| Paket | Isi |
|---|---|
| `cloudx-common` | `/opt/cloudx/venv` (dependensi + `shared`), user `cloudx`, `/etc/cloudx/cloudx.conf` (dibuat bila belum ada, secret dibangkitkan seperti `install-systemd.sh`), `/var/lib/cloudx`, tmpfiles untuk `/run/cloudx`, `/usr/bin/cloudx-manage`, `/usr/share/cloudx/manifest.toml` |
| `cloudx-db` | migrasi Alembic dan `cloudx-manage db sync`; tidak berjalan otomatis |
| `cloudx-<unit>` (satu per unit) | kode `services/<dir>/`, unit systemd, wrapper `/usr/bin/cloudx-<unit>`, `/etc/cloudx/<unit>/<unit>.conf` (conffile), untuk `api` bergaya nginx juga `/etc/nginx/conf.d/cloudx-<unit>.conf` |
| `cloudx-controller`, `cloudx-compute`, `cloudx-storage` | meta, `Depends` pada paket dengan `roles` terkait; `Recommends` PostgreSQL, RabbitMQ, memcached, OVN (tidak dikonfigurasi paket) |
| `cloudx-cli` | `cloudx` CLI dengan venv kecil sendiri |

Semua paket per unit `Depends: cloudx-common (= versi)`. Versi dari `VERSION`, revisi `-1`. venv dibangun saat build pada path akhir `/opt/cloudx/venv`, shebang ditulis ulang, `Depends: python3 (>= 3.12), python3 (<< 3.13)`. Skrip host yang sekarang (`setup-compute-node.sh`, `setup-ceph-libvirt-secret.sh`) dipasang sebagai `/usr/sbin/cloudx-setup-compute` dan `/usr/sbin/cloudx-setup-ceph-secret` di `cloudx-compute`, tidak dijalankan otomatis karena butuh IP dan keyring.

Perilaku pemasangan mengikuti OpenStack di Ubuntu: unit di-enable dan distart saat dipasang (hormati `policy-rc.d`); operator mengisi config, menjalankan `cloudx-manage db sync`, lalu me-restart. Upgrade yang membawa migrasi baru membuat service menolak start sampai `db sync`, sama seperti sekarang.

### 3. Runtime API: `shared/launcher.py`
Wrapper `/usr/bin/cloudx-<unit>` hanya memanggil `shared.launcher.run_api(name, app_dir, port, workers, frontend)`, yang memuat config (`load_service_config`), lalu `execv` gunicorn dengan `-k uvicorn.workers.UvicornWorker`.
- `workers`: urutan prioritas `<unit>.conf` > `cloudx.conf` > manifest. Kunci `workers` di conf menggantikan `-w` yang sekarang tertanam di unit.
- Bind: `frontend=nginx` memakai `unix:/run/cloudx/<unit>.sock`; `frontend=direct` memakai `0.0.0.0:<port>` seperti sekarang.
- Unit: `User=` dari manifest, `Group=cloudx`, `UMask=0007` (socket berizin 0660, grup `cloudx`), `Restart=always`, `RestartSec=5s`.
- **`--forwarded-allow-ips` tidak boleh `*`.** Uvicorn dengan `*` memakai entri pertama `X-Forwarded-For` sebagai klien, dan entri itu dikirim klien (bisa dipalsukan). Untuk `frontend=nginx` nilainya kosong sehingga header diabaikan.
- Gunicorn tetap dipakai: OpenStack menyebutnya server yang valid, dan tidak ada alasan teknis menggantinya. Ganti ke `uvicorn --workers` hanya mengubah launcher.
- Daemon: wrapper memanggil `run_daemon(name, app_dir)` yang `chdir` ke `app_dir` lalu menjalankan `app.main` (`runpy`), jadi unit hanya berisi `ExecStart=/usr/bin/cloudx-<unit>`.

Nama paket `app` tidak diubah. Mengganti nama ke `cloudx_<svc>` menyentuh semua import dan tes (tes mengimpor `services.<dir>.app...`) dan manfaatnya hanya kerapian; ditunda.

### 4. Pintu depan: nginx
Satu snippet per API di `/etc/nginx/conf.d/cloudx-<unit>.conf`, conffile yang dapat disunting:
- `upstream` ke unix socket, `server { listen <port>; location / { proxy_pass ...; } }`, `proxy_http_version 1.1`, `proxy_set_header Host`, dan `X-Forwarded-For $remote_addr` (menimpa, bukan menambahkan).
- `streaming = true`: `client_max_body_size 0`, `proxy_request_buffering off`, `proxy_read_timeout 3600` (setara pengecualian chunked untuk Glance dan Swift di devstack).
- Drop-in `/etc/systemd/system/nginx.service.d/cloudx.conf` dengan `SupplementaryGroups=cloudx` supaya worker nginx bisa membuka socket tanpa mengubah keanggotaan grup user `www-data`. Postinst me-restart nginx sekali saat drop-in dibuat, dan `nginx -t` sebelum reload; bila gagal, paket tetap terpasang dan pesan error dicetak.
- Port luar sama dengan sekarang, jadi `*_api_url`, CLI, dan dashboard tidak berubah. Path-based routing (`/image`) tidak termasuk: butuh `root_path` di tiap aplikasi dan berisiko merusak tautan di respons.

`frontend = "nginx"` untuk 20 API: auth 8005, billing 8011, block-store 8004, compute 8003, container 8018, database 8019, dns 8016, event 8012, file-store 8017, ha 8020, image 8002, k8s-engine 8015, key-manager 8014, load-balancer 8008, messaging 8022, networking 8001, orchestration 8013, placement 8006, reservation 8023, telemetry 8010.

`frontend = "direct"` (tetap TCP, tanpa nginx) untuk 4 service: object-store 8007, metadata-api 8775, metadata-proxy 8776, dashboard 8080. Alasan: keempatnya memakai IP klien (lihat Keamanan); dashboard juga memakai WebSocket.

### 5. Daemon
Console script per unit (lihat 3). Daemon CloudX satu proses dan tidak butuh launcher bergaya `oslo.service`. Konfigurasi `oslo.config` tidak ditiru: menulis ulang pemuatan config demi opsi yang terdaftar di kode berbiaya besar dan manfaatnya kecil.

### 6. Service catalog
**Sumber kebenaran.** `shared/services.py` memuat tabel (`catalog_name`, `catalog_type`, `url_key`, port bawaan). `auth/app/config.py` memakainya menggantikan `_CLOUDX_SERVICES` yang kini terpotong. Tes memastikan tabel dan manifest sama.

**Mengisi catalog.** `cloudx-manage catalog sync [--dry-run]` membaca `url_key` dari conf, lalu menambah service dan memperbarui endpoint (`public` dan `internal`) yang URL-nya berbeda; mencetak tiap perubahan. Kunci opsional `<url_key>_public` (misalnya `image_api_public_url`) mengisi endpoint `public` bila berbeda dari `internal`. Bootstrap awal di auth memakai fungsi yang sama untuk membuat entri baru. Entri yang sudah ada hanya diubah oleh `sync`, tidak oleh start biasa, supaya perubahan admin tidak tertimpa diam-diam.

**Membaca catalog.** `shared/catalog.py`: `url(service, interface="internal")` menyelesaikan dengan urutan: (1) kunci `<url_key>` di conf atau env bila ada (override, setara `endpoint_override` di keystoneauth); (2) catalog dari auth lewat `GET /internal/catalog` (header `X-Internal-Token`, mengikuti konvensi `/internal/*`), di-cache `catalog_cache_ttl` (default 60 s), dan sekali di-refresh bila koneksi ke URL hasil catalog gagal; (3) `CatalogError` yang menyebut nama service. `auth_api_url` tetap wajib di conf (titik awal, seperti `auth_url` di OpenStack).

**Klien.** CLI memakai `catalog` dari respons login (interface `public`), dengan `CLOUDX_<SVC>_URL` sebagai override dan `127.0.0.1:<port>` hanya bila catalog tidak memuat service itu.

**Adopsi.** Urutan override-dulu berarti tidak ada yang berubah sampai URL dikeluarkan dari conf: aman dan dapat dibatalkan per langkah. Setelah 38 modul pindah ke `shared.catalog`, conf bawaan hanya berisi `auth_api_url`, dan satu tes gagal bila ada modul di luar `shared/catalog.py` (dan bootstrap auth) yang membaca `*_API_URL` langsung.

### 7. `cloudx-manage`
Satu perintah untuk operator, mengikuti `<svc>-manage` OpenStack: `cloudx-manage db sync` (membungkus `shared.migrate`), `cloudx-manage catalog sync`. `cloudx-db-migrate` tetap ada sebagai alias.

### 8. Build
`scripts/build-deb.sh` (target `make deb`) membaca manifest, menyusun staging per paket, memanggil `fakeroot dpkg-deb --build`, hasil di `dist/deb/`. Membuat arsip apt dan penandatanganan tidak termasuk; untuk lab cukup `dpkg-scanpackages` ke repo lokal (dijelaskan di dokumen). `scripts/build-tarball.sh` tidak diubah.

## Keamanan: IP klien di belakang proxy
Di belakang nginx lewat unix socket, `request.client` kosong. `object_store/app/main.py:_classify_source` lalu menganggap `src_ip = 127.0.0.1` dan memercayai `X-Forwarded-For`; bila header itu tidak ada, semua klien dianggap INTERNAL dan bucket privat terbuka. Memperbaikinya benar-benar butuh model "trusted proxy" (hop paling kanan yang bukan proxy tepercaya adalah klien), dan rantai dashboard ke object-store menambah satu hop yang harus dilewati. Itu perubahan perilaku yang terpisah dari paket ini, jadi:
- object-store, dashboard, metadata-api, dan metadata-proxy tetap `direct` di rilis pertama.
- Tindak lanjut (spec sendiri): model trusted-proxy untuk keempatnya, lalu pindah ke nginx.
- Untuk 20 API yang di-nginx: sudah diperiksa, tidak ada yang membaca IP klien (`grep request.client` hanya menemukan empat service di atas).

## Tidak termasuk
TLS di nginx (kini hanya perubahan config, spec terpisah); routing berbasis path; arsip apt dan penandatanganan; mengganti nama paket `app`; config gaya `oslo.config`; HA PostgreSQL/RabbitMQ; distro atau arsitektur selain Ubuntu 24.04 amd64; venv per service; kontainer; kepatuhan penuh kebijakan arsip Debian (venv bawaan dan `/opt`, tidak untuk arsip resmi Debian).

## Pengujian
- **Unit:** manifest valid (nama unik, port unik, `url_key` ada di tabel catalog); generator unit dan snippet nginx (golden file: `streaming`, `frontend=direct` tanpa snippet); argv launcher (urutan prioritas `workers`, bind unix vs TCP, `--forwarded-allow-ips` tidak pernah `*`); resolusi catalog (override, cache, refresh sekali, `CatalogError`); `catalog sync` (menambah, memperbarui, `--dry-run`, tidak menyentuh entri yang sama); `shared/services.py` sama dengan manifest.
- **Paket:** `dpkg-deb --info/--contents` pada hasil build: file ada, `Depends` benar, conffile bertanda, tidak ada unit di `/etc/systemd/system`.
- **Lab (node atau VM Ubuntu 24.04 bersih):** pasang `cloudx-common`, `cloudx-db`, dan dua paket API; `db sync`; kedua API menjawab di port luar lewat nginx; unit hidup setelah reboot; catalog berisi URL benar setelah `catalog sync`; `cloudx image list` dari node lain lewat catalog tanpa env.
- Suite penuh dan lint tetap hijau setiap fase.

## Fase (masing-masing dapat dirilis sendiri)
1. **Fondasi:** manifest, `launcher`, `cloudx-manage`, `build-deb.sh`, paket `cloudx-common`/`cloudx-db`/per unit, semua `frontend=direct` (perilaku runtime sama dengan sekarang).
2. **Catalog:** `shared/services.py`, `catalog sync`, `/internal/catalog`, CLI membaca catalog. Berguna sendiri karena catalog lab sekarang salah.
3. **nginx dan socket** untuk 20 API (membalik `frontend` di manifest).
4. **Paket meta, `cloudx-cli`, dokumen `docs/deb-install.md`, runbook upgrade node lab.**
5. **Adopsi catalog** di 38 modul dan tes lint; URL keluar dari conf bawaan.
6. *(spec terpisah)* trusted-proxy untuk object-store, dashboard, dan metadata.

Fase 2 tidak bergantung pada 1. Fase 3 dan 4 bergantung pada 1. Fase 5 bergantung pada 2.

## Risiko dan penanganan
- **Salah mengelompokkan IP klien:** diatasi dengan `direct` untuk empat service dan tes launcher (tanpa `*`).
- **venv tidak dapat dipindah:** dibangun di path akhir, shebang ditulis ulang; tes lab menjalankan `import shared` dari paket terpasang. Venv terikat Python 3.12 (`Depends`).
- **Unit lama menimpa unit paket:** node hasil tarball punya `/etc/systemd/system/cloudx-*.service` yang mengalahkan `/lib/systemd/system`. Runbook upgrade mewajibkan menghapus unit lama dan `systemctl disable --now` sebelum memasang paket; conffile `/etc/cloudx/*` dipertahankan (`--force-confold`).
- **nginx dan gunicorn berebut port saat migrasi:** runbook menghentikan unit lama dulu; nginx baru dimuat ulang setelah itu.
- **Service start sebelum dikonfigurasi:** `Restart=always`, seperti sekarang; paket mencetak langkah berikutnya (isi config, `db sync`, restart).
- **Unit root (block-store-api, dashboard, metadata):** socket berizin 0660 grup `cloudx` karena `UMask=0007` dan `Group=cloudx`; diuji untuk block-store-api.
