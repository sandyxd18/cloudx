# Database as a Service (setara Trove)

PostgreSQL dan MariaDB sebagai VM atau container, dibuat dengan satu panggilan. Setara fitur Trove dasar, bukan 1:1.

## Arsitektur

- `database-api` (controller, port 8019): tabel `db_instances`. Tidak ada worker baru; layanan ini memanggil compute, block-store, file-store, container dan networking lewat API mereka dengan token pemanggil.
- Create dan delete berjalan di thread latar. Status: `building`, `active`, `stopped`, `deleting`, `error`. Baris `building`/`deleting` yang tertinggal saat API restart ditandai `error` (hapus ulang untuk membersihkan).
- Tiap instance mendapat security group sendiri: ingress port engine dari `allowed_cidr` (default CIDR subnet pertama jaringan), egress allow-all. `0.0.0.0/0`, IPv6 dan prefix lebih lebar dari /8 ditolak.
- Sandi admin diacak bila tidak diberikan (`A-Za-z0-9_-`, 12-64 karakter), dikembalikan sekali saat create, tidak disimpan.

## Matriks

| backend | local | block | file |
|---|---|---|---|
| vm + postgresql | ya | ya | ditolak (422) |
| vm + mariadb | ya | ya | ya, tidak disarankan |
| container | ya (volume Docker `ct-<id>-data`) | ditolak | ditolak |

Postgres di atas NFS gagal saat `pg_createcluster` (root squash) di lab, jadi kombinasinya ditolak.

## Kesiapan

- VM: cloud-init memanggil `POST {database_public_url}/internal/db-ready/<id>` dengan header `X-Ready-Token` sekali pakai (hanya sha256 yang disimpan). Batas 900 detik.
- Container: log dipantau sampai baris siap muncul setelah baris init selesai. Batas 300 detik.

## Konfigurasi

`cloudx.conf`: `database_api_url`, `database_public_url`. Yang kedua harus bisa dijangkau dari jaringan tenant (di lab: `http://172.20.0.118:8019`). Pastikan file diakhiri newline sebelum menambah baris.

## CLI

```
cloudx database create pg --engine postgresql --backend vm --network <net> --image <img> --flavor small --storage block --size 5
cloudx database list | show | start | stop | delete
```

## Backup dan restore

Hanya instance VM + block. Backup adalah salinan crash-consistent volume data: block-store-worker membuat snapshot LV, mengekspornya ke bucket backup (MinIO), lalu menghapus snapshot. PostgreSQL dan MariaDB memulihkan diri dari salinan seperti ini lewat WAL atau redo log, seperti setelah listrik padam. Perubahan ini berlaku untuk semua backup volume block-store, bukan hanya database.

```
cloudx database backup create <instance> [--name N]
cloudx database backup list
cloudx database backup restore <backup> <nama-baru> --network <net> --image <img> --flavor <f>
cloudx database backup delete <backup>
```

- Restore selalu membuat instance baru (volume dari backup, VM baru, security group baru). Sandi admin baru diacak dan ditampilkan sekali; sandi lama tidak dipakai lagi (`ALTER USER` saat boot).
- Pakai image Ubuntu yang sama dengan instance asal: versi mayor engine harus sama.
- Backup tetap ada setelah instance dihapus dan harus dihapus sendiri. Backup yang sedang dipakai restore tidak bisa dihapus (409; penjaganya hanya di memori, hilang saat database-api restart).
- Container, storage lokal, dan file ditolak (422).

Pembanding: Trove menyimpan backup di Swift dan restore membuat instance baru (docs.openstack.org/trove, "Backup and restore a database"). Perbedaannya: backup di sini adalah salinan volume, bukan dump logis lewat guest agent.

## Terbukti di lab

container + postgres/mariadb (koneksi, sandi salah ditolak, data bertahan stop/start, delete bersih); vm + postgres + block (koneksi, data); vm + mariadb + file (port terbuka, auth ditolak, SSH tertutup oleh SG). Hapus tidak meninggalkan volume, share, security group.

Backup dan restore, VM + block: PostgreSQL 16 dan MariaDB 10.11, backup diambil saat sebuah klien terus menulis; restore ke instance baru membawa 1000 baris awal beserta baris yang masuk selama backup, sandi lama ditolak dan sandi baru berfungsi, sumber tetap berjalan, snapshot LV tidak tersisa. Lab tidak punya MinIO, jadi `mc` diganti sementara dengan stub HTTP; jalur MinIO asli tidak diuji di sini.
