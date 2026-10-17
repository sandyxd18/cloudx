# Replikasi volume (Fase 3) — desain

## Tujuan
Mengganti `enable/sync/failover/disable-replication` yang kini hanya mengubah kolom status menjadi replikasi data nyata antar node storage LVM. Sukses = data replika identik (sha256) dengan sumber, dan failover mengembalikan volume dengan data itu dari node lain.

## Batasan
- Hanya driver LVM. Driver lain menolak dengan 400 `replication not supported by driver`.
- Tanpa SSH dan tanpa paket baru. Hanya stdlib.
- Auth antar worker: `internal_token` (HMAC), konsisten dengan service-to-service auth yang sudah ada.
- Config lewat `/etc/cloudx/block-store-worker/*.conf`, bukan env di systemd.

## Alur
1. **enable** (`POST /volumes/{ref}/enable-replication`, `target` = host LVM):
   target harus ada di `LVM_HOSTS`, bukan `volume.host`, dan volume `available`/`in-use`. API mengirim RPC ke worker target untuk membuat LV `rep-<volume_id>` berukuran sama, lalu status `enabled`.
2. **sync** (`POST /volumes/{ref}/sync-replication`): status `replicating`. Worker sumber membuat snapshot LVM sementara, mengalirkannya ke receiver di worker target lewat TCP sekali pakai (header: volume, ukuran, HMAC nonce; stream; sha256 di akhir). Target membandingkan sha256, lalu `synced` + `last_replicated_at`. Gagal => `error` + pesan; snapshot sementara selalu dihapus.
3. **failover** (`POST /volumes/{ref}/failover`): LV replika menjadi volume utama (`volume.host = target`, LV direname ke nama volume), status `failed-over`. Ditolak 409 bila volume `in-use`, kecuali `force=true` (node sumber mati). LV lama tidak dihapus otomatis.
4. **disable**: replika dihapus di target, status `disabled`.

## Komponen
- `block_store/app/routers/replication.py`: validasi + RPC (ikuti pola RPC messaging yang ada).
- `block_store_worker/app/drivers/lvm.py`: `replica_create`, `replica_send`, `replica_promote`, `replica_delete`.
- `block_store_worker/app/replication.py` (baru, ±40 baris): receiver TCP (port dari conf, hanya menerima IP host sumber) dan sender.
- Migrasi: hanya jika kolom pesan galat belum ada (cek `replication_*` di model).

## Pengujian
- Unit (mock `run`/socket): validasi target, penolakan driver non-LVM, HMAC salah ditolak, sha256 beda => `error`, snapshot sementara dihapus saat gagal, failover `in-use` tanpa force => 409.
- Live (compute-node → storage-node): tulis pola, sync, md5 replika sama; ubah sumber, sync, md5 sama; hentikan worker sumber, failover `force`, data cocok; disable bersih. Bersihkan semua LV/volume `zz-`.

## Sengaja ditunda (`ponytail:`)
Sync selalu penuh (inkremental via `thin_delta` nanti); sync manual tanpa jadwal; satu replika per volume; tidak ada failback otomatis; `async`/`sync` hanya label; stream tidak dienkripsi (HMAC hanya autentikasi; TLS bila jaringan storage tidak tepercaya).
