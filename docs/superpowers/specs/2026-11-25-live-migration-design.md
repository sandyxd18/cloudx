# Live migration dan pengosongan node compute - desain

Memindahkan server yang sedang jalan ke node compute lain tanpa mematikannya, dan memakainya untuk mengosongkan satu node sebelum maintenance. Mengganti `POST /servers/{ref}/live-migrate` yang sekarang menjawab 501.

## Tujuan dan batas

- Tujuan utama: Sandy bisa mengosongkan sebuah compute node untuk maintenance tanpa mematikan VM yang jalan di dalamnya. Live migration satu server adalah bahan dasarnya.
- Termasuk: `POST /servers/{ref}/live-migrate`, kebijakan `compute:server:live_migrate`, jalur libvirt antar-node lewat `qemu+ssh`, persiapan dan pembersihan di node tujuan, pengecualian di rekonsiliator, perintah CLI pengosongan node yang bisa dijalankan ulang, skrip dan dokumen untuk menyiapkan kepercayaan ssh, dokumen fitur.
- Bukan tujuan: block migration (menyalin LV lokal lewat jaringan), memindahkan volume antar-node, mode multi-chassis OVN untuk jeda paket nol, TLS libvirt, live migration untuk server PAUSED, SUSPENDED, atau dalam status lain selain ACTIVE, tombol dashboard, evacuate otomatis oleh ha-api.
- Anti-tujuan (gagal walau tampak jalan): VM mati atau hilang di tengah migrasi, dua domain yang menulis ke disk yang sama, server tertinggal `MIGRATING` tanpa jalan keluar, node tujuan penuh sisa tap atau sesi iSCSI setelah migrasi gagal, atau pengosongan yang diam-diam melewati VM yang datanya akan hilang bersama node.

## Keadaan sekarang yang membatasi desain

- `live_migrate_server` di `server_actions.py` hanya memeriksa server lalu menjawab 501. `GET /servers/{ref}/migration-candidates` sudah ada. Kebijakan `compute:server:migrate` (admin) ada untuk cold migrate; belum ada untuk live.
- Cold migrate (`ops.py::_relocate` dan `place_and_build`) sudah memilih target lewat `claim_host`, membuat payload build (semua port, volume data, volume root), dan mengganti `server.host`. Worker sumber menjalankan `relocate_out`, yang mematikan domain dulu.
- Node compute menjalankan worker sebagai root (`cloudx-compute-worker.service`), jadi kunci ssh ada di `/root/.ssh`. Belum ada jalur libvirt antar-node, tidak ada `qemu+ssh` dan tidak ada port 16509.
- Disk server: root dan data bisa RBD (semua node membukanya), atau LV yang dimiliki satu node (`volume.host`) dan dijangkau node lain lewat iSCSI (`volume_manager.connect` login dan `disconnect` logout plus `unexport_volume`). XML domain menyimpan path, jadi di node lain path LV iSCSI berbeda (`/dev/disk/by-path/ip-...-iscsi-...`).
- Domain memakai tap `managed="no"` yang dibuat worker dan dipasang ke OVS dengan `external_ids:iface-id` (`network_manager.bind_tap_to_ovs`). Sebuah tap yang sudah punya `iface-id` di dua node membuat ovn-controller berebut port.
- Config drive adalah file ISO di `instance_dir` node (`/var/lib/cloudx/compute/instances/<server_id>/`) dan domain menunjuk ke file itu.
- **`reconcile_stale_domains` (`compute_worker/main.py`) menghancurkan domain lokal mana pun yang menurut API ada di host lain.** Tanpa pengecualian, rekonsiliasi di node sumber atau tujuan bisa membunuh VM di tengah migrasi.
- Penjadwal (`claim_host`) mengecualikan layanan yang `disabled` atau `forced_down`, dan placement mencatat alokasi per server.

## Pendekatan yang dipertimbangkan

| | Isi | Hasil |
|---|---|---|
| A | compute-api memeriksa dan memilih target; worker sumber mengatur seluruh alur (menyiapkan target lewat RPC, migrasi libvirt, pembersihan); transport `qemu+ssh` dengan kepercayaan ssh yang disiapkan operator | **Dipilih** oleh Sandy |
| B | Seperti A, tetapi libvirt TLS | Tidak ada akses ssh antar-root, tetapi butuh sertifikat klien dan server di tiap node: menarik CloudX ke urusan PKI yang sudah diputuskan tidak ditangani |
| C | A ditambah block migration | Node benar-benar kosong, tetapi menyalin disk lewat jaringan, jauh lebih besar dan berisiko; ditunda |

Pengosongan node dibuat di CLI, bukan di server: tidak ada status di API yang bisa tertinggal kalau proses berhenti di tengah, dan perintahnya aman dijalankan ulang. Pola "satu thread di proses API" sengaja dihindari (stack orkestrasi tertahan `*_IN_PROGRESS` karena itu).

## Desain

### API

`POST /servers/{ref}/live-migrate`, body `{"host": "<node>"}` (opsional; kosong = penjadwal memilih). Kebijakan baru `compute:server:live_migrate`, aturan bawaan `role:admin` (kebijakan yang sama ditambahkan di `shared/policy.py`, `docs/policy.md`, `etc/cloudx/policy.yaml.sample`, dan jumlah 57 aksi di dokumen serta tes ikut diperbarui). Menjawab 202 dengan `{"server_id", "from", "to", "status": "MIGRATING"}`, bentuk yang sama dengan `migrate`.

Pemeriksaan sebelum apa pun berubah (409 dengan alasan yang menyebut sumbernya, atau 404 untuk server yang tidak terlihat):
1. Status server `ACTIVE`; server tidak terkunci.
2. Server punya host dan volume root; setiap volume terpasang berstatus `in-use` dengan path terisi.
3. Host tujuan (bila diminta) ada, tidak `disabled`, tidak `forced_down`, bukan host sumber. Tanpa host diminta, `claim_host(..., avoid=(sumber,))` memilih; tidak ada kandidat = 409 `No valid host was found`.

Alokasi placement dipindah ke tujuan saat server masuk `MIGRATING` (claim ulang dengan consumer yang sama). Bila migrasi gagal, alokasi dikembalikan ke sumber dengan claim `only_hosts=[sumber]`.

`GET /servers/{ref}/migration-candidates` dibiarkan; builder boleh membuatnya mengecualikan node `disabled` dan `forced_down` bila itu murah.

### Alur (diatur worker sumber)

compute-api: validasi, claim, ubah status ke `MIGRATING`, `record_action("live_migrate")`, lalu cast `live_migrate` ke worker sumber dengan payload server, port, daftar volume (id, host, path, target_dev, backend), dan `_target_host`. Setelah itu compute-api tidak ikut campur lagi.

Worker sumber (`handle_live_migrate`, menggantikan handler yang membaca `server_id` yang salah):

1. **Persiapan target.** `rpc.call` ke antrean host tujuan, metode `live_migrate_prepare` (pola yang sama dengan `export_volume` ke block-store), timeout dari konfigurasi. Worker tujuan:
   - membuat tap tiap port dan memasangnya ke `br-int` **tanpa** `iface-id` (fungsi baru di `network_manager`; `bind_tap_to_ovs` memberi `iface-id` sekaligus dan tidak boleh dipakai di sini);
   - untuk tiap volume LV yang `host` bukan node tujuan, login iSCSI lewat `volume_manager.connect`;
   - membuat ulang config drive di path yang sama dengan sumber (`cloudinit.create_config_drive` dengan payload yang sama) dan memanggil `expose_to_host_libvirt`;
   - membalas `{disk_paths: {volume_id: path}, migration_host}`. Gagal di langkah mana pun: bersihkan yang sudah dibuat dan balas error; sumber berhenti di sini (tidak ada yang berubah di sumber).
2. **XML tujuan.** Ambil `XMLDesc(VIR_DOMAIN_XML_MIGRATABLE)` domain sumber, ganti `<source dev>` tiap disk dengan path dari balasan persiapan (cocokkan lewat path sumber yang sekarang; RBD tidak diubah), biarkan sisanya. Fungsi murni yang bisa diuji dengan XML nyata.
3. **Migrasi.** `migrateToURI3` dengan URI dari `live_migration_uri` (templat, `{host}` diisi `migration_host`), flag `LIVE | PEER2PEER | PERSIST_DEST | UNDEFINE_SOURCE | ABORT_ON_ERROR` (dan `AUTO_CONVERGE` bila dihidupkan), parameter `DEST_XML`, bandwidth, dan max downtime dari konfigurasi. Berjalan di thread terpisah; worker memantau `jobInfo` sampai `live_migration_timeout` lalu `abortJob()`. Penjadwal RPC worker tidak boleh terblokir selama itu (konsumer menjalankan satu handler sekali waktu, seperti yang sudah dijelaskan di komentar `handle_build_instance`): migrasi memakai thread sendiri dan melapor lewat conductor.
4. **Berhasil.** Urutan: (a) worker sumber menghapus tap dan port OVS miliknya (supaya tidak ada dua chassis dengan `iface-id` yang sama); (b) `rpc.call` `live_migrate_finish` ke tujuan, yang memasang `iface-id`, `attached-mac`, `vm-id`, `iface-status` pada tap (`bind_tap_to_ovs` untuk tap yang sudah ada); (c) sumber logout iSCSI dan `unexport_volume` untuk volume yang sumber pakai lewat iSCSI, dan membersihkan `instance_dir` sumber; (d) lewat conductor: `host` = tujuan, `status` = `ACTIVE`, tanpa `error_message`; (e) `_notify_action_finish(..., "success")`. Jeda jaringan antara resume di tujuan dan langkah (b) diperkirakan satu sampai tiga detik; ini yang diukur di lab, dan multi-chassis menjadi pekerjaan lanjutan bila terlalu lama.
5. **Gagal atau dibatalkan.** `abortJob` bila perlu; `rpc.call` `live_migrate_rollback` ke tujuan (hapus tap, logout iSCSI dan unexport untuk sesi yang dibuat persiapan, hapus `instance_dir` tujuan, dan matikan domain tujuan bila libvirt sempat membuatnya); kembalikan alokasi placement; status server kembali `ACTIVE` di sumber tanpa `error_message` (server tidak rusak); `_notify_action_finish(..., "error", alasan)`. Alasan terlihat di catatan aksi server.

### Pengecualian di rekonsiliator

`reconcile_stale_domains` melewati server yang statusnya `MIGRATING`. Ini juga aman untuk cold migrate (domain sumber sudah dihapus `relocate_out` sebelum build). Tes wajib: domain lokal server `MIGRATING` yang menurut API ada di host lain tidak dihancurkan; server `ACTIVE` yang salah host tetap dihancurkan.

### Server yang tertinggal `MIGRATING`

Worker sumber yang berhenti di tengah (crash, restart) tidak boleh meninggalkan server `MIGRATING` selamanya. Saat worker start, untuk server dengan status `MIGRATING` dan `host` sama dengan node ini: bila domainnya berjalan di libvirt lokal, status dikembalikan `ACTIVE`. Bila domain tidak ada di sini, biarkan (itu urusan cold migrate atau evacuate). Syarat lain: operator masih bisa memulihkan lewat `cloudx server start` seperti sekarang. Detailnya diserahkan ke builder, selama kedua syarat itu dipenuhi dan ada tesnya.

### Konfigurasi (`compute-worker.conf`, bagian `[DEFAULT]`, semua opsional)

| Kunci | Bawaan | Arti |
|---|---|---|
| `live_migration_host` | nama node (`COMPUTE_HOST`) | alamat yang dipakai node lain untuk menjangkau node ini |
| `live_migration_uri` | `qemu+ssh://root@{host}/system` | templat URI koneksi libvirt tujuan |
| `live_migration_timeout` | 1800 | detik total sebelum migrasi dibatalkan |
| `live_migration_max_downtime_ms` | 300 | batas jeda saat perpindahan terakhir |
| `live_migration_bandwidth_mbps` | 0 | 0 = tanpa batas |
| `live_migration_auto_converge` | false | perlambat vCPU agar migrasi yang tidak konvergen selesai |

### Kepercayaan ssh dan jaringan (disiapkan operator)

Paket tidak membuat kunci. Dokumen baru `docs/live-migration.md` dan skrip `scripts/setup-migration-ssh.sh NODE...` (dijalankan dari mesin yang bisa ssh ke semua node): membuat kunci `/root/.ssh/cloudx_migration` di tiap node bila belum ada, menaruh kunci publik tiap node ke `authorized_keys` root node lain, dan mengisi `known_hosts`. Skrip menampilkan apa yang dilakukannya dan idempoten. Port TCP 49152-49215 antar compute node harus terbuka (data memori QEMU) dan port 22 antar compute node untuk koneksi libvirt. Dokumen menyebut akibat keamanannya: node compute saling punya akses root.

### Pengosongan node (CLI)

Perintah di grup CLI yang sudah membungkus `/os-services` (builder memilih nama yang sejalan, misalnya `cloudx compute-service drain NODE`):
1. `PUT /os-services/{node}` `disabled=true`, `disabled_reason="drain"`.
2. Daftar server di node itu (admin, semua proyek).
3. Per server, satu per satu (paralelisme 1, bisa dinaikkan dengan `--parallel N` bila murah): `ACTIVE` dengan semua disk yang bisa dipindah lewat live migration: live migration, tunggu sampai `ACTIVE` dengan host berbeda atau sampai gagal; `SHUTOFF`: cold migrate yang sudah ada; selain itu: dilewati dengan alasan.
4. **Dilewati dengan alasan jelas, tidak dipindah diam-diam:** server yang punya LV yang `host`-nya node ini (datanya hilang bersama node, walau VM-nya bisa pindah), server `PAUSED`, `SUSPENDED`, `ERROR`, `BUILD`, dan seterusnya, serta server yang migrasinya gagal.
5. Cetak tabel (server, hasil, alasan) dan keluar dengan status bukan nol bila ada yang dilewati atau gagal. Menjalankan ulang hanya memproses yang masih tersisa. `--dry-run` hanya mencetak rencana. Node dibiarkan `disabled` di akhir (operator mengaktifkannya lagi setelah maintenance).

Pemeriksaan "LV di node ini" memakai `volume.host`, jadi CLI membaca volume lewat block-store. Server yang sama bisa dipindah dengan `live-migrate` biasa (bukan lewat pengosongan) walau LV-nya di node sumber, karena tujuan lalu membaca LV itu lewat iSCSI dari sumber.

### Data dan kompatibilitas

Tidak ada perubahan skema. Tambahan pada pesan RPC (`live_migrate_prepare`, `live_migrate_finish`, `live_migrate_rollback`) hanya menyentuh worker; worker lama menjawab metode yang tidak dikenal dengan galat, yang membuat migrasi gagal dengan alasan itu. Urutan upgrade: semua compute node dulu, lalu compute-api. Konsumer RPC tidak boleh macet menunggu: panggilan `rpc.call` antar worker memakai timeout.

### Tes (tanpa lab)

Tes unit dengan libvirt dan RPC palsu: penulisan ulang XML (RBD tidak berubah, LV iSCSI diganti, config drive tetap); pemeriksaan kelayakan (tiap penolakan 409 dan alasannya); urutan langkah worker dan rollback di tiap titik gagal (persiapan gagal, migrasi gagal, `finish` gagal); pengecualian rekonsiliator; alokasi placement dikembalikan; pemulihan `MIGRATING` saat worker start; kebijakan baru (jumlah aksi, sampel, dokumen); logika drain di CLI (urutan, dilewati, ulang, `--dry-run`, kode keluar); skrip ssh (dry-run tanpa perubahan). Prosedur lab (dijalankan hanya atas perintah Sandy): dua compute node, root RBD dan data LV dari node storage lain, VM klien yang melakukan ping berkelanjutan untuk mengukur jeda, lalu live migrate satu server, kegagalan yang disuntik (ssh ke tujuan diputus di tengah migrasi), dan pengosongan satu node.

### Dokumen

`docs/live-migration.md` baru; `docs/feature-comparison.md` (baris live migration jadi tersedia, dengan batasnya); `docs/storage-backends.md` dan `docs/scenarios.md` (kalimat "tidak didukung" diganti); `docs/policy.md` dan sampel; `docs/user-guide-cli.md`; `CHANGELOG.md` bagian Unreleased.

## Diserahkan ke builder

Nama perintah dan grup CLI untuk pengosongan; tempat fungsi baru di `network_manager` dan `libvirt_driver`; nama kunci konfigurasi bila ada benturan; apakah `migration-candidates` ikut disaring; cara persis worker memulihkan `MIGRATING` saat start.

## Keputusan yang saya ambil sendiri (periksa)

- Pengosongan di CLI dan bukan endpoint server.
- Server yang punya LV di node yang dikosongkan dilewati, bukan dipindah, walau live-migrate biasa mengizinkannya.
- Server `SHUTOFF` dalam pengosongan memakai cold migrate yang sudah ada.
- Bawaan `live_migration_uri` memakai `root@`, karena worker sudah root dan tidak ada akun migrasi.
- Jeda jaringan satu sampai tiga detik diterima untuk versi pertama.
