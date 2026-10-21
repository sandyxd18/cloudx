# Instance HA (setara Masakari)

Memulihkan VM yang dilindungi secara otomatis saat node compute mati. Setara fitur dasar Masakari (host monitor dan host recovery), bukan 1:1.

## Arsitektur

- `ha-api` (controller, port 8020, satu proses dengan satu thread monitor). Tabel `ha_hosts`, `ha_protected`, `ha_recoveries`. Tidak ada worker baru.
- `cloudx-compute-worker` mengirim heartbeat tiap 10 detik ke `POST /internal/heartbeat/{host}` bersama `initiator_iqn` node itu. Gagal kirim hanya dicatat.
- block-store punya endpoint internal baru `POST /internal/fence`: meminta block-store-worker pemilik tiap volume menjalankan `unexport_volume` (mencabut ACL iSCSI). Hasil dikembalikan per volume.
- compute: `PUT /os-services/{host}` dan `POST /servers/{id}/evacuate` kini menerima token internal selain admin.

## Alur

1. Monitor memeriksa tiap 10 detik. Host `up` dengan heartbeat lebih tua dari 60 detik menjadi `suspect`.
2. Setelah 30 detik masih diam, host dianggap mati. Heartbeat yang kembali sebelum itu membatalkan semuanya (tanpa fencing, tanpa evacuate).
3. Hanya VM di `ha_protected` yang diproses. Tanpa VM terlindungi, host cukup ditandai `forced_down`.
4. Pra-cek: VM yang volume root atau datanya ada di host yang mati, atau lokasinya tidak diketahui, dicatat `skipped` dan tidak dievakuasi.
5. Urutan wajib: `forced_down=true`, cabut ACL iSCSI semua volume VM yang lolos, baru `evacuate`. Bila fencing gagal tidak ada evacuate; host tetap `suspect` dan dicoba lagi, maksimal 3 kali, lalu berhenti sampai operator turun tangan.
6. Tiap VM diverifikasi `ACTIVE` dalam 300 detik. Hasil ada di `GET /recoveries` (`done`, `failed`, `skipped`).
7. Host menjadi `fenced`. Heartbeat yang kembali tidak mengubah apa pun.

## Pemulihan node (manual)

1. Pastikan node sehat; VM lama di node itu mungkin masih berjalan dan harus dibersihkan (`virsh destroy` dan `undefine`, logout iSCSI).
2. `PUT /os-services/{host}` dengan `{"forced_down": false}`.
3. `cloudx ha reset HOST`.

## Watchdog (self-fencing)

Tanpa BMC di lab, node yang terisolasi mematikan sendiri VM terlindungi miliknya. Unit `cloudx-ha-watchdog` (root, di setiap node compute, terpisah dari compute-worker) menyimpan daftar VM terlindungi dari ha-api. Bila ha-api tidak terjangkau 45 detik, ia menanyai peer (`GET /reach`):

- ada peer yang masih menjangkau ha-api, atau tidak ada peer yang menjawab: node ini terisolasi, VM terlindungi di-`virsh destroy`;
- semua peer juga tidak menjangkau ha-api: ha-api atau controller yang mati, tidak ada tindakan;
- tanpa peer terkonfigurasi atau daftar kosong: tidak ada tindakan.

Sebelum `destroy`, watchdog menulis `/var/lib/cloudx/ha-watchdog/fenced`; selama file itu ada, compute-worker tidak menghidupkan lagi VM yang mati. File dihapus watchdog setelah operator mengembalikan host ke `up` (`forced_down=false`, lalu `cloudx ha reset HOST`). Respons HTTP apa pun dari ha-api (termasuk 5xx) dianggap terjangkau. `ha_watchdog_dry_run = true` hanya mencatat log.

Konfigurasi `/etc/cloudx/ha-watchdog/ha-watchdog.conf` (`[DEFAULT]`): `ha_watchdog_host` (nama node), `ha_watchdog_peers` (URL peer, dipisah koma), `ha_watchdog_timeout`, `ha_watchdog_dry_run`, `ha_watchdog_port` (8021). Node yang menjalankan watchdog juga harus punya `ha_api_url` di conf yang sama, dipisah koma bila ada beberapa instance ha-api (`ha_api_url = http://a:8020,http://b:8020`). Override ini mengalahkan service catalog, jadi keputusan fencing tidak bergantung pada pencarian catalog: watchdog yang start dingin saat auth tidak terjangkau akan membaca pencarian yang gagal itu sebagai "ha-api diam". Catalog juga hanya menyimpan URL pertama dari daftar berkoma, jadi failover antar instance butuh override ini. Migrasi `cloudx-db-migrate ha` (`ha_0002`). ha-api menaruh "no watchdog seen" di catatan recovery bila host tidak mengirim sinyal watchdog dalam 120 detik terakhir.

Pembanding: Masakari mendeteksi lewat Pacemaker/Corosync dan memakai STONITH (IPMI) sebagai fencing eksternal (docs.openstack.org/masakari-monitors, hostmonitor). Watchdog ini bukan pengganti 1:1: ia bergantung pada node yang sehat cukup untuk mematikan VM-nya sendiri.

## Memakai

- CLI: `cloudx ha hosts | protect SERVER | unprotect SERVER | protected | recoveries | reset HOST`.
- Dashboard: High availability.
- Sebuah VM hanya bisa dilindungi bila semua volumenya ada di host lain dan lokasinya diketahui (422 bila tidak).

## Konfigurasi

- Controller: `ha_api_url` di `cloudx.conf`. Unit `cloudx-ha-api`, migrasi `cloudx-db-migrate ha`.
- Setiap node compute: `ha_api_url = http://<controller>:8020` di `cloudx.conf`.

## Hasil uji lab

Dua VM CirrOS (satu root di storage-node, satu di compute-node), keduanya dilindungi.

- Positif: `cloudx-compute-worker` dan libvirtd di compute-node dihentikan. Sekitar 100 detik kemudian host `fenced`, VM `ha1` pindah ke storage-node dan `ACTIVE`, recovery `done`. Sesi iSCSI di target nol dan target dibongkar. qemu lama tetap hidup di node mati dengan sesi iSCSI yang sudah terputus; ia tidak bisa menulis lagi.
- Negatif: storage-node dimatikan. VM yang root-nya ada di storage-node dicatat `skipped` (`volume on failed host`) dan tidak dievakuasi. VM lain dievakuasi.
- Blip: worker dihentikan 20 detik lalu dinyalakan. Tidak ada tindakan; host kembali `up`.
- Temuan saat uji: `PUT /os-services` dan `evacuate` semula hanya menerima JWT admin sehingga ha-api ditolak (401). Diperbaiki dengan `AdminOrInternal`.

## Dua instance ha-api

Instance kedua boleh dijalankan di node lain (unit `cloudx-ha-api` yang sama). Semua endpoint aktif di keduanya; hanya loop monitor yang dijalankan satu instance, pemegang `pg_try_advisory_lock` pada PostgreSQL bersama. Lock lepas otomatis saat proses atau koneksinya mati, lalu instance lain mengambilnya dalam sekitar 10 detik. Pemimpin baru memberi semua host `last_seen` baru (masa 60 detik sebelum dinilai).

Pasang di node kedua:
1. `/etc/cloudx/ha-api/ha-api.conf` (`[DEFAULT]`) dengan `database_url` ke PostgreSQL controller, dan `compute_api_url`, `block_store_api_url`, `auth_api_url` ke controller.
2. Di controller: baris `pg_hba.conf` untuk IP node itu saja (`/32`, `scram-sha-256`) dan buka 5432 hanya dari node itu.
3. Di semua node compute: `ha_api_url = http://controller:8020,http://node2:8020`. compute-worker mengirim heartbeat ke instance pertama yang menjawab; watchdog menganggap ha-api terjangkau bila salah satu menjawab.

Batas: PostgreSQL, compute-api, block-store-api dan auth tetap di controller. Bila controller mati, instance kedua hidup tetapi tidak bisa mengevakuasi. Dashboard hanya tahu satu URL ha-api.

Hasil uji lab (dua instance: controller dan compute-node): tepat satu leader; ha-api controller dihentikan lalu compute-node mengambil lock dalam 2 detik; dengan leader lama mati, compute-worker dan libvirtd di compute-node dihentikan dan instance kedua mengevakuasi VM terlindungi ke storage-node (recovery `done`); ha-api lama dinyalakan lagi, menjadi follower, dan tidak ada evakuasi ganda. Dengan controller ha-api mati, heartbeat dan proteksi tetap bisa lewat instance kedua.

## Pembersihan setelah evacuate

Saat node lama pulih, compute-worker menghapus domain yang menurut API sudah pindah host, lalu logout sesi iSCSI dari disk domain itu. Sesi yatim tanpa domain tidak dibersihkan.

## Batas

Lihat catatan rilis (`docs/releases/v0.8.5.md`).
