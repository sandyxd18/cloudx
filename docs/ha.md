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

## Batas

Lihat catatan rilis (`docs/releases/v0.8.5.md`).
