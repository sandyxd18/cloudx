# Pemasangan dengan paket .deb

Cara memasang CloudX di Ubuntu 24.04 (amd64) dengan `apt`, satu paket per service, tanpa `scripts/install-systemd.sh`. Rancangannya ada di `superpowers/specs/2026-10-21-openstack-style-packaging-design.md`. Dokumen ini berisi cara membangun paket, memasangnya per peran, menyebar service ke beberapa node, mengoperasikannya, dan mengupgrade node lab yang dipasang dari tarball.

Penting: runbook upgrade di bagian 7 belum diuji di node nyata. Jalankan di satu node dulu.

## 1. Isi paket

`make deb` menghasilkan 47 paket di `dist/deb/`. Versi paket adalah isi `VERSION` ditambah `-1` (misalnya `0.8.6-1`).

| Paket | Isi |
|---|---|
| `cloudx-common` | `/opt/cloudx/venv` (dependensi dan modul `shared`), user dan grup `cloudx`, `/etc/cloudx/cloudx.conf` (dibuat saat pemasangan pertama), `/var/lib/cloudx`, `/run/cloudx` (tmpfiles), `/usr/bin/cloudx-manage` |
| `cloudx-db` | migrasi Alembic untuk `cloudx-manage db sync`; tidak berjalan sendiri |
| `cloudx-<unit>` (41 paket) | kode service di `/opt/cloudx/services/<dir>`, unit systemd di `/lib/systemd/system`, perintah `/usr/bin/cloudx-<unit>`, conf `/etc/cloudx/<unit>/<unit>.conf`; API di belakang nginx juga membawa `/etc/nginx/conf.d/cloudx-<unit>.conf` |
| `cloudx-controller`, `cloudx-compute`, `cloudx-storage` | paket meta: `Depends` ke paket service sesuai peran, `Recommends` ke `cloudx-cli` dan paket distro (lihat di bawah) |
| `cloudx-cli` | perintah `cloudx` dengan venv sendiri di `/opt/cloudx/cli/venv`, bisa dipasang sendirian |

Dua unit BIND (`cloudx-named-auth`, `cloudx-named-resolver`) ikut di dalam `cloudx-dns-worker` dan tidak punya paket sendiri.

### Peran

| Paket meta | Service |
|---|---|
| `cloudx-controller` | `auth-api`, `billing-api`, `billing-worker`, `block-store-api`, `compute-api`, `conductor`, `container-api`, `dashboard`, `database-api`, `dns-api`, `event-api`, `event-worker`, `file-store-api`, `ha-api`, `image-api`, `image-worker`, `k8s-engine-api`, `k8s-engine-worker`, `key-manager-api`, `load-balancer-api`, `messaging-api`, `metadata-api`, `networking-api`, `object-store-api`, `orchestration-api`, `orchestration-worker`, `placement-api`, `reaper`, `reservation-api`, `telemetry-api`, ditambah `cloudx-db`. `cloudx-dns-worker` hanya `Recommends` (lihat bagian 8) |
| `cloudx-compute` | `bgp-worker`, `compute-worker`, `ha-watchdog`, `lb-worker`, `metadata-proxy`, `telemetry-worker`, `vpn-worker`, ditambah `cloudx-setup-compute` dan `cloudx-setup-ceph-secret` di `/usr/sbin` |
| `cloudx-storage` | `block-store-worker` |

`cloudx-file-store-worker` dan `cloudx-container-worker` tidak masuk peran mana pun: keduanya terikat ke satu node tertentu dan dipasang sendiri.

Paket meta juga `Recommends` ke `cloudx-cli` dan paket distro yang tidak dikonfigurasi oleh CloudX: PostgreSQL, RabbitMQ, memcached, dan OVN Central di controller; libvirt, QEMU, Open vSwitch, `ovn-host`, dan `qemu-utils` di compute. `apt install` memasang `Recommends` secara bawaan. Untuk `cloudx-controller` itu berarti `cloudx-dns-worker` dan `bind9` ikut terpasang, dan `named` bawaan distro langsung berebut port 53 sebelum host disiapkan (bagian 8). Karena itu bagian 4.1 memasang controller dengan `--no-install-recommends` dan menyebut paket Recommends-nya satu per satu, kecuali `cloudx-dns-worker`. Hilangkan PostgreSQL dan RabbitMQ dari daftar itu bila keduanya ada di host lain.

### API dan port

Port luar sama dengan instalasi tarball.

| Depan | Service (port) |
|---|---|
| nginx (20) | `auth-api` 8005, `billing-api` 8011, `block-store-api` 8004, `compute-api` 8003, `container-api` 8018, `database-api` 8019, `dns-api` 8016, `event-api` 8012, `file-store-api` 8017, `ha-api` 8020, `image-api` 8002, `k8s-engine-api` 8015, `key-manager-api` 8014, `load-balancer-api` 8008, `messaging-api` 8022, `networking-api` 8001, `orchestration-api` 8013, `placement-api` 8006, `reservation-api` 8023, `telemetry-api` 8010 |
| langsung TCP (4) | `object-store-api` 8007, `metadata-api` 8775, `metadata-proxy` 8776, `dashboard` 8080 |

API di belakang nginx mendengarkan di `unix:/run/cloudx/<unit>.sock`; nginx mendengarkan di port luarnya.

## 2. Membangun paket

Di Ubuntu 24.04 amd64 (venv yang dibangun berisi wheel biner dan terikat Python 3.12):

```bash
sudo apt install dpkg-dev fakeroot python3-venv
make deb
ls dist/deb | wc -l        # 47
```

Syarat:

- `dpkg-deb`, `fakeroot`, dan `python3` 3.12 dengan modul `venv`.
- Jaringan: `pip` mengisi `/opt/cloudx/venv` (di dalam `cloudx-common`) dan venv CLI saat build.
- Aset statis dashboard harus sudah ada di `services/dashboard/app/static/`. `make deb` tidak membangunnya (`make package` yang membangunnya). Bila belum ada:

  ```bash
  (cd services/dashboard/frontend && npm ci && npm run build)
  mkdir -p services/dashboard/app/static
  cp -r services/dashboard/frontend/dist/* services/dashboard/app/static/
  ```

`make deb` menghapus isi `dist/deb/` lebih dulu. Memeriksa hasilnya:

```bash
dpkg-deb --info dist/deb/cloudx-image-api_*_amd64.deb
dpkg-deb --contents dist/deb/cloudx-image-api_*_amd64.deb
```

## 3. Repositori apt lokal untuk lab

Arsip apt resmi dan penandatanganan tidak termasuk. Untuk lab cukup repositori datar:

```bash
cd dist/deb
dpkg-scanpackages . | gzip -9c > Packages.gz
```

Di node yang memasang (salin direktori `dist/deb` ke sana, misalnya ke `/srv/cloudx-repo`, atau sajikan lewat HTTP dari mesin build dengan `python3 -m http.server`):

```bash
echo 'deb [trusted=yes] file:/srv/cloudx-repo ./' | sudo tee /etc/apt/sources.list.d/cloudx.list
# lewat HTTP:  deb [trusted=yes] http://<mesin-build>:8000 ./
sudo apt update
```

`trusted=yes` melewati pemeriksaan tanda tangan; pakai hanya di jaringan lab. Tanpa repositori, `apt` juga bisa memasang berkas langsung dan menyelesaikan dependensi di antara berkas yang disebut bersamaan:

```bash
sudo apt install ./cloudx-common_*.deb ./cloudx-image-api_*.deb
```

Paket `nginx`, `init-system-helpers`, dan `python3` diambil dari repositori Ubuntu.

## 4. Memasang per peran

Semua unit di-enable dan distart oleh paket saat dipasang (mengikuti `policy-rc.d`). Sebelum database dan config siap, service akan gagal start dan dicoba lagi tiap 5 detik (`Restart=always`); itu normal sampai langkah `db sync` dan restart di bawah selesai.

### 4.1 Controller

Urutan pertama kali:

1. Pasang paketnya.

   ```bash
   sudo apt install --no-install-recommends cloudx-controller \
        postgresql rabbitmq-server memcached ovn-central cloudx-cli
   ```

   Daftar sesudah `cloudx-controller` adalah `Recommends` paket meta itu tanpa `cloudx-dns-worker`. DNS bersifat opsional dan butuh persiapan host lebih dulu, jadi dipasang terpisah di langkah 8.

2. Siapkan prasyarat yang tidak dikonfigurasi paket. Ikuti `../SETUP.md`: 2.2 (PostgreSQL: listen ke jaringan manajemen, user `cloudx`, database `cloudx`, `cloudx_orchestration`, `cloudx_events`, `cloudx_billing`, `cloudx_k8s_engine`, `cloudx_key_manager`), 2.3 (user RabbitMQ `cloudx`), dan 2.4 (listener OVN Central 6641 dan 6642). Di Ubuntu 24.04 PostgreSQL adalah versi 16, jadi `14` di jalur `/etc/postgresql/14/main` pada SETUP.md dibaca `16`. Bagian 2.5 di SETUP.md (tarball, venv, unit, migrasi) tidak dipakai; 2.6 dan 2.7 tetap berlaku.

3. Sunting `/etc/cloudx/cloudx.conf`.

   Lima secret (`auth_secret_key`, `auth_admin_password`, `key_manager_master_key`, `metrics_token`, `internal_token`) dibangkitkan paket saat pemasangan pertama. Jangan diganti: mengganti `auth_secret_key` membatalkan semua token. Pemasangan ulang dan upgrade hanya mengisi secret yang kosong dan tidak pernah mengubah yang sudah ada. File ini berizin `640 root:cloudx`.

   Kunci yang perlu Anda isi atau periksa:

   | Kunci | Isi |
   |---|---|
   | `database_url` | PostgreSQL, bawaan `postgresql+psycopg2://cloudx:cloudx@127.0.0.1:5432/cloudx` (sesuai SETUP.md 2.2) |
   | `rabbitmq_url` | RabbitMQ, bawaan `amqp://cloudx:cloudx@127.0.0.1:5672/` (sesuai SETUP.md 2.3) |
   | `memcached_url` | bawaan `127.0.0.1:11211` |
   | `auth_api_url` | alamat auth yang bisa dijangkau node lain, misalnya `http://192.168.10.10:8005`, bukan `127.0.0.1` |

   Kunci lain di `/usr/share/cloudx/cloudx.conf.sample` diisi sesuai fitur yang dipakai (`dns_*`, `file_store_host`, `container_host`, `storage_lvm_driver`, `lvm_vg_name`).

   Password admin: `sudo grep auth_admin_password /etc/cloudx/cloudx.conf`.

4. Buat skema database. Layanan menolak start sebelum ini.

   ```bash
   sudo cloudx-manage db sync
   ```

   `cloudx-db-migrate` tetap ada sebagai alias. Aman dijalankan ulang.

5. Tulis URL service di `/etc/cloudx/auth-api/auth-api.conf`.

   Instalasi satu node: lewati langkah ini dan langkah 6. Bawaannya (`http://127.0.0.1:<port>`) sudah benar.

   Banyak node: auth mendaftarkan setiap service saat start pertama dari kunci `<nama>_api_url` yang ia baca, atau `http://127.0.0.1:<port>` bila tidak ada, dan sesudah itu tidak pernah mengubah entri yang sudah ada. `/etc/cloudx/cloudx.conf` bawaan paket hanya berisi `auth_api_url` di antara kunci `*_api_url` (contoh kunci lainnya dikomentari), jadi `catalog sync` tidak punya URL lain untuk dibaca kecuali Anda menulisnya di tempat yang dibaca auth, yaitu `/etc/cloudx/auth-api/auth-api.conf` di node yang menjalankan `auth-api`. Tulis alamat yang bisa dijangkau node lain untuk setiap service yang tidak berjalan di node auth, dan juga untuk yang berjalan di node auth bila node lain atau CLI perlu menjangkaunya (`127.0.0.1` hanya terjangkau dari node itu sendiri):

   ```ini
   [DEFAULT]
   image_api_url = http://infra02:8002
   compute_api_url = http://192.168.10.10:8003
   # opsional: URL yang diberikan ke pengguna bila berbeda dari yang dipakai antar service
   # image_api_public_url = http://cloud.example.com:8002
   ```

   Kuncinya adalah kolom `url_key` di `services/shared/services.py`: `compute_api_url`, `image_api_url`, `networking_api_url`, `block_store_api_url`, `placement_api_url`, `object_store_api_url`, `load_balancer_api_url`, `metadata_api_url`, `telemetry_api_url`, `billing_api_url`, `event_api_url`, `orchestration_api_url`, `key_manager_api_url`, `k8s_engine_api_url`, `dns_api_url`, `file_store_api_url`, `container_api_url`, `database_api_url`, `ha_api_url`, `messaging_api_url`, `reservation_api_url` (dan `auth_api_url` dari `cloudx.conf`). Varian publiknya `<nama>_api_public_url`. Bila nilainya beberapa URL dipisah koma (`ha_api_url`), yang didaftarkan hanya URL pertama.

6. Isi service catalog di auth.

   ```bash
   sudo cloudx-manage catalog sync --dry-run   # hanya mencetak perubahan
   sudo cloudx-manage catalog sync
   ```

   Baca keluaran `--dry-run` dulu: `+` berarti service atau endpoint baru, `~` berarti URL yang sudah terdaftar diubah (`<lama> -> <baru>`). Sesudah `db sync`, auth biasanya sudah sempat start dan mendaftarkan semua service dengan `127.0.0.1`, jadi banyak baris `~` di sini normal. Jalankan tanpa `--dry-run` bila daftarnya benar. Bagian 5 menjelaskan aturan lengkapnya.

7. Restart service dan periksa.

   ```bash
   sudo systemctl restart 'cloudx-*'
   systemctl --no-pager status 'cloudx-*'
   curl -s http://127.0.0.1:8005/health
   cloudx auth login --username admin --auth-url http://192.168.10.10:8005
   ```

8. (Opsional) DNS. Siapkan host dulu seperti di bagian 8 ("DNS"), lalu pasang `cloudx-dns-worker`.

Jaringan provider dan floating IP dibuat setelah itu (`../SETUP.md` 2.7).

### 4.2 Node compute

1. Salin `/etc/cloudx/cloudx.conf` dari controller ke node ini sebelum memasang, supaya secret sama di semua node. Paket tidak menimpa file yang sudah ada. Grup `cloudx` belum ada sebelum paket dipasang, jadi file dipasang dulu dengan izin `600 root:root`; postinst `cloudx-common` mengubahnya menjadi `640 root:cloudx`.

   ```bash
   # ~/cloudx.conf adalah salinan dari controller (salin dengan izin 600 dan hapus sesudahnya: berisi secret)
   sudo install -d -m 750 /etc/cloudx
   sudo install -m 600 -o root -g root ~/cloudx.conf /etc/cloudx/cloudx.conf
   rm ~/cloudx.conf
   ```

   Bila paket sudah terlanjur dipasang, ganti file hasil bangkitan dengan salinan dari controller dalam satu perintah yang langsung memberi izin benar, lalu restart service:

   ```bash
   sudo install -m 640 -o root -g cloudx ~/cloudx.conf /etc/cloudx/cloudx.conf
   ```

2. Pasang paketnya dan jalankan skrip host.

   ```bash
   sudo apt install cloudx-compute
   sudo cloudx-setup-compute --controller-ip 192.168.10.10 --node-ip 192.168.10.11 \
        --node-name cloudx-compute-01 --provider-interface enp0s9
   ```

   Opsi lain yang diterima: `--central-ip` (sama dengan `--controller-ip`), `--encap-ip` (sama dengan `--node-ip`), `--provider-if`. Nilai juga bisa lewat variabel `CONTROLLER_IP`, `NODE_IP`, `NODE_NAME`, `PROVIDER_IF`. Bawaannya `192.168.10.10`, `192.168.10.11`, `cloudx-compute-01`, `enp0s9`. Nama node harus unik di antara node compute.

   Skrip ini tidak dijalankan oleh paket karena butuh IP dan nama interface. Yang dikerjakannya:

   | Langkah | Isi |
   |---|---|
   | 1 | `apt-get install` paket host: QEMU/KVM, libvirt, Open vSwitch, `ovn-host`, strongSwan, BIRD, HAProxy, cloud-image-utils, dan alat build untuk `libvirt-python`; menonaktifkan `strongswan`, `strongswan-starter`, dan `bird` bawaan distro; mematikan profil AppArmor `charon` dan `swanctl` |
   | 2 | `systemctl enable --now libvirtd openvswitch-switch` |
   | 3 | mengatur OVN: `ovn-remote=tcp:<controller>:6642`, `ovn-encap-type=geneve`, `ovn-encap-ip`, `system-id=<nama node>`; restart `ovn-controller` |
   | 4 | membuat bridge `br-ex` dan memasang interface provider ke dalamnya |
   | 5 | menulis `/etc/cloudx/<svc>/<svc>.conf` untuk `compute-worker`, `metadata-proxy`, `telemetry-worker`, `lb-worker`, `vpn-worker`, dan `bgp-worker`, berisi `compute_host`, `node_name`, `host_ip`, `rabbitmq_url`, `database_url`, dan `auth_api_url` yang mengarah ke controller; tidak ada `*_api_url` lain |

   Alamat service lain (placement, networking, compute, image, dan seterusnya) tidak ditulis di enam file itu. Worker di node ini mengambilnya dari catalog di auth, jadi isi catalog dengan alamat yang bisa dijangkau dari node compute (4.1 langkah 5 dan 6) sebelum memulai worker. Bila catalog belum berisi sebuah service, worker melaporkan `CatalogError` yang menyebut nama service itu.

   Skrip tidak mengisi `ovn-bridge-mappings` dan `ovn-cms-options` (lihat `../SETUP.md` 4.3 bila jaringan provider dipakai). Kredensial di `rabbitmq_url` dan `database_url` yang ditulisnya adalah `cloudx:cloudx`; ubah di enam file itu bila berbeda. Pesan penutup skrip menyebut `install-systemd.sh`; abaikan, paketnya sudah terpasang.

   Enam file itu conffile paket dan skrip menimpanya setiap dijalankan, termasuk suntingan manual sebelumnya. Karena isinya berbeda dari conf bawaan paket, `dpkg` menanyakan apa yang dilakukan pada upgrade yang membawa conf baru. Pasang dengan `--force-confold` (bagian 6.4) supaya file Anda dipertahankan.

3. Pasang binding libvirt ke venv paket. Tanpanya `compute-worker` tetap start tetapi tidak bisa membuat VM (SETUP.md 4.1). Alat build-nya sudah dipasang oleh langkah 1 di atas.

   ```bash
   sudo /opt/cloudx/venv/bin/pip install libvirt-python
   ```

   `dpkg` tidak melacak file itu, jadi ia tidak ikut terhapus saat upgrade `cloudx-common`; pasang ulang bila venv dibangun ulang.

4. Restart service compute.

   ```bash
   sudo systemctl restart cloudx-compute-worker cloudx-metadata-proxy cloudx-telemetry-worker \
        cloudx-lb-worker cloudx-vpn-worker cloudx-bgp-worker cloudx-ha-watchdog
   ```

   Pengaturan `ha-watchdog` ada di `ha.md`.

5. Node dengan disk Ceph RBD: setelah `/etc/ceph/ceph.conf` dan keyring user (bawaan `client.cloudx`) ada di node ini, jalankan:

   ```bash
   sudo cloudx-setup-ceph-secret                      # user cloudx, keyring /etc/ceph/ceph.client.cloudx.keyring
   sudo cloudx-setup-ceph-secret --user cloudx --keyring /etc/ceph/ceph.client.cloudx.keyring
   sudo systemctl restart cloudx-compute-worker
   ```

   Skrip mendefinisikan secret libvirt dari keyring (memakai ulang UUID yang sudah ada), lalu menulis `ceph_user` dan `ceph_secret_uuid` ke `/etc/cloudx/compute-worker/compute-worker.conf`. Aman dijalankan ulang. Klien Ceph dan keyring tidak dipasang oleh paket.

### 4.3 Node storage

```bash
# cloudx.conf disalin dari controller seperti di 4.2
sudo apt install cloudx-storage
```

`block-store-worker` butuh pengaturan per node di `/etc/cloudx/block-store-worker/block-store-worker.conf`: `block_store_host`, `iscsi_export`, `iscsi_portal_ip`, `storage_lvm_driver`, `lvm_pv_name`, `lvm_vg_name`, ditambah `rabbitmq_url` dan `database_url` ke controller. Kunci per node itu ditulis oleh `scripts/prepare-lvm-node.sh --host <nama> --portal-ip <ip> [--device /dev/sdb]`, dijalankan dari checkout repo (skrip ini tidak ada di paket; langkah lengkapnya di `two-node-lvm-lab.md`). Skrip menimpa file conf itu dan tidak menulis `rabbitmq_url` maupun `database_url`, jadi tambahkan keduanya sesudahnya.

### 4.4 CLI saja

```bash
sudo apt install cloudx-cli
cloudx auth login --username admin --auth-url http://192.168.10.10:8005
```

## 5. Menyebar service ke beberapa node

Satu paket API bisa dipasang di node mana saja, tidak harus di controller. Pemasangan di node lain butuh:

- `/etc/cloudx/cloudx.conf` yang sama dengan controller (secret identik), dengan `auth_api_url` yang menunjuk ke auth.
- `database_url` dan `rabbitmq_url` yang menunjuk ke PostgreSQL dan RabbitMQ (di conf node itu atau di conf unitnya).
- `cloudx-manage db sync` tidak perlu dijalankan di sana; skema dibuat sekali dari node mana saja yang punya `cloudx-db` dan akses ke database.

Service saling menemukan lewat service catalog di auth. Urutan pencarian URL: kunci `<nama>_api_url` di conf atau environment bila ada (override), lalu catalog dari `GET /internal/catalog` (cache `catalog_cache_ttl`, bawaan 60 detik), lalu galat `CatalogError` yang menyebut nama service. Hanya `auth_api_url` yang wajib ada di setiap node.

URL setiap service hanya ditulis di satu tempat, yaitu tempat catalog diisi: `/etc/cloudx/auth-api/auth-api.conf` di node yang menjalankan `auth-api`. Jangan menaruhnya di `cloudx.conf`: kunci di sana dibaca semua service sebagai override dan mengalahkan catalog. Untuk instalasi satu node bawaan `127.0.0.1` sudah benar dan tidak ada yang perlu ditulis.

Memasang atau memindahkan sebuah API ke node lain:

1. Pasang paket API di node tujuan (dengan `cloudx.conf` yang sama, lihat atas).
2. Di node auth, tulis URL service itu di `/etc/cloudx/auth-api/auth-api.conf`, dengan alamat yang bisa dijangkau dari node lain:

   ```ini
   [DEFAULT]
   image_api_url = http://192.168.10.20:8002
   ```

3. Jalankan `sudo cloudx-manage catalog sync --dry-run` di node auth, baca baris `+` dan `~`, lalu jalankan `sudo cloudx-manage catalog sync` tanpa `--dry-run`.
4. Hentikan dan lepas paket API di node lama bila sebelumnya ada.

Daftar kunci ada di 4.1 langkah 5.

`catalog sync` hanya menambah service dan memperbarui endpoint yang URL-nya berbeda, mencetak setiap perubahan, dan tidak pernah menghapus. Kunci yang tidak ada atau kosong dibiarkan. Bila nilainya beberapa URL dipisah koma (misalnya `ha_api_url`), hanya URL pertama yang didaftarkan. Kunci `<nama>_api_public_url` (misalnya `image_api_public_url`) mengisi endpoint `public` bila berbeda dari `internal`. Start biasa hanya mendaftarkan service yang belum punya endpoint dan tidak mengubah yang sudah ada; hanya `catalog sync` yang mengubahnya. Jadi suntingan admin lewat API tidak tertimpa diam-diam, dan URL baru di `auth-api.conf` tidak berlaku sebelum `catalog sync` dijalankan. Service lain mengambil perubahan dalam satu masa cache, tanpa restart.

CLI memakai endpoint `public` dari catalog di respons login. `CLOUDX_<SVC>_URL` tetap berlaku sebagai override.

### Yang terikat ke node

| Komponen | Keterikatan |
|---|---|
| `block-store-worker` | node tempat volume berada (nama node di `block_store_host`, daftar node di `BLOCK_STORE_LVM_HOSTS` pada controller, lihat `two-node-lvm-lab.md`) |
| `file-store-worker` | node yang namanya sama dengan `file_store_host`; butuh NFS-Ganesha |
| `container-worker` | tiap node yang diisi `container_host`; butuh Docker |
| `dns-worker` dan BIND | satu node dengan dua IP: `dns_public_address` (auth) dan `dns_resolver_address` (resolver) |
| `compute-worker` dan worker compute lain | satu per node compute, nama node unik |
| OVN Central, PostgreSQL, RabbitMQ | satu instance, tidak diatur paket; HA-nya di luar cakupan |

`ha-api` boleh berjalan di dua node (lihat `ha.md`).

## 6. Operasi

### 6.1 Jumlah worker API

Kunci `workers` di `/etc/cloudx/<unit>/<unit>.conf` menentukan jumlah worker gunicorn. Prioritas: conf unit, lalu `cloudx.conf`, lalu nilai bawaan yang ditanam di `/usr/bin/cloudx-<unit>` saat build (dari `packaging/manifest.toml`). `/usr/share/cloudx/manifest.toml` hanya salinan untuk dibaca; menyuntingnya tidak mengubah apa pun.

```ini
[DEFAULT]
workers = 4
```

```bash
sudo systemctl restart cloudx-compute-api
```

Nilai yang bukan bilangan bulat positif (`0`, `-1`, `abc`, kosong) membuat unit berhenti dengan pesan yang menyebut unit dan kuncinya, lalu dicoba lagi tiap 5 detik. Kunci ini hanya untuk unit API.

### 6.2 Status dan log

```bash
systemctl --no-pager status 'cloudx-*'
systemctl is-active cloudx-image-api
journalctl -u cloudx-image-api -f
sudo nginx -t
```

`journalctl` per unit seperti di instalasi tarball. Log nginx ada di `/var/log/nginx/`.

### 6.3 Menghapus paket API

`apt remove cloudx-image-api` (contoh) menghentikan unit, tetapi conffile tetap ada, termasuk snippet nginx. Port luarnya lalu menjawab `502` dari nginx karena socket-nya hilang, sampai paket dipasang lagi atau snippet dihapus. Paket tidak me-reload nginx saat dilepas. `apt purge` menghapus snippet dan conf unit itu (termasuk suntingan Anda); setelahnya tetap jalankan:

```bash
sudo systemctl reload nginx
```

### 6.4 Conffile dan upgrade

| File | Perlakuan |
|---|---|
| `/etc/cloudx/<unit>/<unit>.conf` | conffile paket unit |
| `/etc/nginx/conf.d/cloudx-<unit>.conf` | conffile paket API di belakang nginx |
| `/etc/cloudx/cloudx.conf` | bukan conffile; dibuat `cloudx-common` bila belum ada dan tidak pernah ditimpa |

`dpkg` hanya bertanya bila paket baru membawa isi conffile yang berbeda dan file di disk sudah Anda ubah. Untuk memasang tanpa pertanyaan dan mempertahankan file Anda:

```bash
sudo apt install -o Dpkg::Options::=--force-confold cloudx-image-api
sudo apt upgrade -o Dpkg::Options::=--force-confold
```

Upgrade versi: paket me-restart unitnya sendiri. Bila versi baru membawa migrasi, service menolak start sampai `sudo cloudx-manage db sync` dijalankan di controller; setelah itu `sudo systemctl restart 'cloudx-*'`.

Pemasangan API baru me-reload nginx otomatis; bila `nginx -t` gagal, paket tetap terpasang dan pesan galat dicetak. Perbaiki config lalu `sudo systemctl reload nginx`.

## 7. Upgrade node lab dari tarball

Belum diverifikasi di node nyata. Urutan dan perintah di bawah disusun dari isi paket dan dari `install-systemd.sh`; jalankan dulu di satu node berisiko rendah (bukan node auth), periksa, baru lanjut. Catat hasilnya di dokumen ini setelah dicoba.

Mengapa tidak bisa langsung `apt install`:

- Unit lama di `/etc/systemd/system/cloudx-*.service` menimpa unit paket di `/lib/systemd/system`, jadi unit lama harus dihapus.
- `dpkg` menolak menimpa file yang bukan miliknya. Isi `/opt/cloudx` dari tarball (venv, `services`, `cli`) bentrok dengan isi paket, jadi harus disingkirkan.
- nginx harus bisa memakai port yang dulu dipegang gunicorn, jadi unit lama harus berhenti dulu.
- `/etc/cloudx` dan `/var/lib/cloudx` dipertahankan.

Urutan antar node: node yang hanya menjalankan beberapa service dulu, node dengan auth dan PostgreSQL terakhir. Selama satu node dikerjakan, service di node itu mati (beberapa menit). Setelah naik ke paket, URL antar-node tidak berubah karena port luar sama. Kunci `*_api_url` yang masih ada di conf lama kini berlaku sebagai override yang mengalahkan catalog, jadi service di node itu tetap memakai alamat lama sampai kunci itu dihapus; periksa isi catalog dengan `catalog sync --dry-run` (langkah 7) sebelum menghapusnya.

### Per node

1. Periksa dan catat keadaan awal.

   ```bash
   # lima secret harus terisi (hasil 5), dan nilainya harus sama di semua node
   sudo grep -cE '^(auth_secret_key|auth_admin_password|key_manager_master_key|metrics_token|internal_token) *= *[^ ]' /etc/cloudx/cloudx.conf

   systemctl list-units 'cloudx-*.service' --state=active --no-legend | awk '{print $1}' | sudo tee /root/cloudx-active-before.txt
   sudo tar czf /root/cloudx-etc.tgz /etc/cloudx                    # berisi secret, simpan di tempat aman
   sudo tar czf /root/cloudx-units.tgz /etc/systemd/system/cloudx-*
   ls -d /etc/systemd/system/cloudx-*.service.d 2>/dev/null
   ```

   Direktori `*.service.d` (drop-in, misalnya `provider-network.conf` dari SETUP.md 2.7) tetap berlaku sesudah unit lama dihapus dan bisa menimpa unit paket. Pindahkan isinya ke conf unit (`Environment=PROVIDER_CIDR=...` menjadi `provider_cidr = ...`) lalu hapus drop-in-nya.

2. Hentikan dan nonaktifkan semua unit lama, lalu hapus.

   ```bash
   systemctl list-unit-files 'cloudx-*.service' --state=enabled --no-legend | awk '{print $1}' | xargs -r sudo systemctl disable --now
   sudo systemctl stop 'cloudx-*'
   sudo rm /etc/systemd/system/cloudx-*.service
   sudo systemctl daemon-reload
   ```

   `systemctl disable` tidak menerima pola `cloudx-*`, jadi daftar unit diambil dulu.

3. Singkirkan isi tarball dan tautan lamanya.

   ```bash
   sudo mv /opt/cloudx /opt/cloudx.tarball
   [ -L /usr/local/bin/cloudx ] && sudo rm /usr/local/bin/cloudx   # tautan ke venv lama, menutupi /usr/bin/cloudx
   ```

4. Pasang paket untuk setiap service yang tadi berjalan di node ini.

   ```bash
   PKGS=$(sed -e 's/\.service$//' -e '/^cloudx-named-/d' /root/cloudx-active-before.txt)
   sudo apt update
   sudo apt install -o Dpkg::Options::=--force-confold cloudx-common $PKGS
   ```

   Di node yang dulu menjalankan `make migrate` tambahkan `cloudx-db`; di node yang memakai CLI tambahkan `cloudx-cli`. Unit BIND ikut di `cloudx-dns-worker`. Jangan pakai `./cloudx-*.deb`: itu memasang ke-47 paket, termasuk semua paket meta.

   `--force-confold` mempertahankan `/etc/cloudx/<unit>/<unit>.conf` yang sudah ada. `cloudx.conf` lama dipakai apa adanya, termasuk kunci `*_api_url` di dalamnya (override); paket hanya mengisi secret yang kosong dan mengatur ulang izinnya menjadi `root:cloudx` `640`.

5. Pastikan nginx memuat snippet baru.

   ```bash
   sudo nginx -t
   sudo systemctl restart nginx
   ```

6. Node compute: pasang lagi `libvirt-python`. Venv tarball memilikinya, venv paket tidak. Tanpa itu `compute-worker` jatuh ke `virsh` dan `telemetry-worker` tidak bisa mengimpor libvirt, sementara systemd tetap melaporkan unitnya `active`.

   ```bash
   sudo /opt/cloudx/venv/bin/pip install libvirt-python
   sudo systemctl restart 'cloudx-*'
   ```

   Alat build yang dibutuhkan (`libvirt-dev`, `pkg-config`, `build-essential`, `python3-dev`) sudah ada dari `setup-compute-node.sh` yang dulu dijalankan; bila tidak, lihat 4.2 langkah 2 dan 3.

7. Verifikasi.

   ```bash
   # unit yang aktif sama dengan sebelumnya (selisih hanya boleh unit cloudx-named-*)
   diff <(sort /root/cloudx-active-before.txt) <(systemctl list-units 'cloudx-*.service' --state=active --no-legend | awk '{print $1}' | sort)

   # tiap API yang terpasang di node ini; port yang tidak terpasang menjawab 000
   for p in 8001 8002 8003 8004 8005 8006 8008 8010 8011 8012 8013 8014 8015 8016 8017 8018 8019 8020 8022 8023; do
       printf '%s ' "$p"; curl -s -o /dev/null -w '%{http_code}\n' --max-time 5 "http://127.0.0.1:$p/health"
   done
   ```

   Semua API di node ini harus `200`. Ulangi `curl` dari node lain dengan `http://<node>:<port>/health` untuk memastikan nginx menjawab di alamat luar. Empat service langsung (8007, 8775, 8776, 8080) diperiksa dengan `systemctl is-active`.

   Di node dengan auth:

   ```bash
   sudo cloudx-manage db sync                  # tidak mengubah apa pun bila skema sudah mutakhir
   sudo cloudx-manage catalog sync --dry-run   # hanya mencetak: "catalog already up to date" atau daftar perubahan
   ```

   Di node compute, `import libvirt` harus berhasil (status `active` saja tidak membuktikannya):

   ```bash
   /opt/cloudx/venv/bin/python -c "import libvirt"
   ```

   Dari node yang punya CLI: `cloudx server list` harus tetap bekerja.

### Bila gagal

Bahan untuk kembali: `/opt/cloudx.tarball`, `/root/cloudx-units.tgz` (unit lama), `/root/cloudx-etc.tgz` (cadangan `/etc/cloudx`), dan `/root/cloudx-active-before.txt`. Prosedur ini juga belum dicoba. Urutannya penting karena nginx masih memegang port 8001 sampai 8023 selama snippet paket ada.

1. Hentikan unit paket dan lepas paketnya. Pakai `remove`, bukan `purge`: `purge` ikut menghapus `/etc/cloudx/<unit>/<unit>.conf`, yang di node ini adalah file Anda.

   ```bash
   sudo systemctl stop 'cloudx-*'
   sudo apt remove $(dpkg-query -W -f='${Package} ' 'cloudx-*')
   ```

2. Buang snippet nginx (conffile tetap ada setelah `remove`) dan lepaskan port, sebelum unit lama dinyalakan:

   ```bash
   sudo rm /etc/nginx/conf.d/cloudx-*.conf
   sudo nginx -t && sudo systemctl reload nginx
   ```

3. Kembalikan isi tarball. Isi `/opt/cloudx` dari paket dibuang dulu (tarball asli aman di `/opt/cloudx.tarball`):

   ```bash
   sudo rm -rf /opt/cloudx
   sudo mv /opt/cloudx.tarball /opt/cloudx
   sudo ln -s /opt/cloudx/venv/bin/cloudx /usr/local/bin/cloudx   # hanya bila tautan ini ada sebelumnya
   ```

4. Kembalikan unit lama saja, lalu nyalakan unit yang tadi aktif:

   ```bash
   sudo tar xzf /root/cloudx-units.tgz -C / etc/systemd/system
   sudo systemctl daemon-reload
   grep -v '^cloudx-named-' /root/cloudx-active-before.txt | xargs -r sudo systemctl enable --now
   ```

Langkah di atas tidak menyentuh `/etc/cloudx`, dan `cloudx-etc.tgz` jangan dibuka menyeluruh ke `/`: itu mengembalikan `cloudx.conf` ke keadaan cadangan. Pulihkan hanya file yang memang perlu dibatalkan, misalnya:

```bash
sudo tar xzf /root/cloudx-etc.tgz -C / etc/cloudx/image-api/image-api.conf
```

Bila Anda sudah terlanjur memakai `apt purge`, pulihkan `/etc/cloudx/<unit>/<unit>.conf` yang terhapus dengan cara yang sama, satu per satu, dan jangan menyentuh `etc/cloudx/cloudx.conf`.

## 8. Batas dan langkah manual

| Hal | Keterangan |
|---|---|
| Socket | gunicorn membuat `/run/cloudx/<unit>.sock` berizin `0777`, jadi semua user lokal di host bisa menyambung. Jangkauannya sama dengan listener TCP `0.0.0.0` yang digantikan; semua rute tetap butuh token atau token internal. Jangan menambah `www-data` ke grup `cloudx`: worker nginx jadi bisa membaca `/etc/cloudx` yang berisi secret |
| Batas bawaan nginx | 19 API non-streaming memakai bawaan nginx: `client_max_body_size 1m` dan `proxy_read_timeout 60s`. Bila ada permintaan dengan body besar atau yang lebih lama, tambahkan di blok `location /` pada `/etc/nginx/conf.d/cloudx-<unit>.conf`, misalnya `client_max_body_size 20m;` atau `proxy_read_timeout 300;`, lalu `sudo systemctl reload nginx`. `image-api` sudah dikecualikan (`client_max_body_size 0`, `proxy_request_buffering off`, `proxy_read_timeout 3600`) |
| Empat service `direct` | `object-store-api`, `metadata-api`, `metadata-proxy`, dan `dashboard` tetap mendengarkan langsung di TCP. Keempatnya memutuskan berdasarkan IP klien; di belakang nginx lewat unix socket `request.client` kosong dan `object-store-api` menganggap semua klien internal bila header `X-Forwarded-For` tidak ada, sehingga bucket privat terbuka. Model trusted-proxy untuk keempatnya adalah spec lanjutan. `dashboard` juga memakai WebSocket |
| TLS dan path routing | belum ada. nginx hanya mendengarkan HTTP di port luar masing-masing; tidak ada routing berbasis path (`/image`) |
| `nginx -t` gagal saat pasang | pemasangan paket API tidak gagal; pesan `warning: nginx -t failed` dicetak. Periksa dengan `sudo nginx -t`, perbaiki, lalu `sudo systemctl reload nginx`. Port yang masih dipegang proses lain (misalnya gunicorn lama) membuat reload gagal mengikat port; hentikan proses itu dan jalankan `sudo systemctl restart nginx` |
| Arsitektur dan distro | hanya Ubuntu 24.04 amd64 dengan Python 3.12 |

### Persiapan host yang tidak dikerjakan paket

`install-systemd.sh` mengerjakan beberapa hal di host yang tidak dikerjakan paket. Lakukan manual di node yang bersangkutan.

**DNS (controller, `cloudx-dns-worker`).** Paket ini hanya `Recommends` dari `cloudx-controller` karena menarik `bind9`, dan instance `named` bawaan distro akan berebut port 53. Karena itu 4.1 tidak memasangnya. Siapkan host dulu, baru pasang:

```bash
sudo apt install bind9 bind9utils
sudo systemctl disable --now named bind9 || true   # di Ubuntu 24.04 salah satu nama bisa berupa alias
systemctl is-active bind9                          # harus inactive
sudo mkdir -p /var/lib/cloudx/dns/auth /var/lib/cloudx/dns/resolver
echo '/var/lib/cloudx/dns/** rwk,' | sudo tee /etc/apparmor.d/local/usr.sbin.named
sudo apparmor_parser -r /etc/apparmor.d/usr.sbin.named
sudo apt install cloudx-dns-worker
```

Isi `dns_public_address` (BIND otoritatif) dan `dns_resolver_address` (BIND resolver) di `cloudx.conf` dengan dua IP yang terpasang di interface node itu; BIND tidak boleh mengikat `0.0.0.0` karena `systemd-resolved` memakai port 53 di localhost (`dns.md`).

**Prometheus (controller).** Prometheus membaca `/metrics` dengan token dari `metrics_token`. Bila Prometheus terpasang di node yang sama:

```bash
( umask 037; sudo grep '^metrics_token' /etc/cloudx/cloudx.conf | cut -d= -f2- | tr -d ' \n' | sudo tee /etc/prometheus/metrics_token >/dev/null )
sudo chgrp prometheus /etc/prometheus/metrics_token
sudo chmod 640 /etc/prometheus/metrics_token
```

**File store dan container.** Node `file-store-worker` butuh `nfs-ganesha` dan `nfs-ganesha-vfs`, dengan `sudo systemctl disable --now nfs-ganesha` (instance distro berebut port 2049). Node `container-worker` butuh `docker.io` dengan `/etc/docker/daemon.json` berisi `{"iptables": false, "bridge": "none", "ip-forward": false}` supaya Docker tidak menyentuh iptables dan OVN.

**Volume group LVM.** `install-systemd.sh` memanggil `scripts/setup-storage.sh` untuk `vg-cloudx` lokal. Di node storage, pakai `scripts/prepare-lvm-node.sh` (4.3).

## 9. Tarball tetap berjalan

`scripts/install-systemd.sh`, `scripts/build-tarball.sh` (`make install-systemd`, `make package`, `make migrate`) tidak diubah dan masih berfungsi untuk node yang belum dialihkan. Jangan memakai keduanya di node yang sama: unit di `/etc/systemd/system` dan isi `/opt/cloudx` dari tarball bentrok dengan paket. Node bertarball dan node berpaket bisa berada dalam satu cluster, karena port luar dan protokol antar service tidak berubah. Satu syarat: kode tarball yang memuat pencarian lewat catalog mengambil URL dari `GET /internal/catalog` di auth bila `*_api_url` tidak ada di conf node itu. Naikkan auth lebih dulu (atau biarkan kunci `*_api_url` di conf node bertarball sebagai override), dan isi catalog (bagian 5) sebelum node yang tidak punya kunci itu dijalankan.
