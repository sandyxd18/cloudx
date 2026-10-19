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

## Terbukti di lab

container + postgres/mariadb (koneksi, sandi salah ditolak, data bertahan stop/start, delete bersih); vm + postgres + block (koneksi, data); vm + mariadb + file (port terbuka, auth ditolak, SSH tertutup oleh SG). Hapus tidak meninggalkan volume, share, security group.
