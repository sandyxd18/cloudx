# Messaging (setara Zaqar)

Antrean pesan per project untuk aplikasi tenant. Produsen mengirim, konsumen mengklaim lalu menghapus pesan yang sudah diproses. Pesan yang klaimnya habis sebelum dihapus muncul lagi (at-least-once).

## Arsitektur

- `messaging-api` (controller, port 8022, `User=cloudx`), tabel `msg_queues` dan `msg_messages` di PostgreSQL. Tanpa worker, thread latar, atau kredensial baru.
- Klaim memakai `SELECT ... FOR UPDATE SKIP LOCKED`, jadi beberapa worker gunicorn tidak mendapat pesan yang sama.
- Urutan FIFO per antrean (id naik). Tidak ada jaminan ketat antar klaim paralel.
- Pesan kedaluwarsa tidak pernah dikembalikan (disaring saat dibaca) dan dihapus saat ada `POST` berikutnya ke antrean itu.

## Batas

Body JSON maks 64 KiB; `ttl` pesan 60 detik sampai 14 hari (default 1 jam); `ttl` klaim 30 detik sampai 12 jam (default 5 menit); maks 10 pesan per `POST`, 20 per klaim, 10.000 pesan per antrean (lebih dari itu 429).

## API

```
PUT    /queues/{name}                       GET /queues        GET /queues/{name}/stats
DELETE /queues/{name}
POST   /queues/{name}/messages              {"messages":[{"body":..., "ttl":3600}]}
GET    /queues/{name}/messages?limit=       (melihat tanpa mengklaim)
POST   /queues/{name}/claims?ttl=&limit=    -> {"claim_id", "items"}
DELETE /queues/{name}/messages/{id}?claim_id=
DELETE /queues/{name}/claims/{claim_id}     (melepas klaim)
```

Menghapus pesan yang sedang diklaim wajib menyertakan `claim_id` yang cocok (409 bila tidak). Antrean diacu dengan nama dan hanya terlihat di project pemiliknya; role `reader` hanya bisa membaca.

## CLI

```
cloudx queue create jobs
cloudx queue post jobs '{"task": "resize", "id": 7}' --ttl 600
cloudx queue claim jobs --ttl 60 --limit 5
cloudx queue ack jobs <message_id> --claim <claim_id>
cloudx queue list | stats | peek | delete
```

## Konfigurasi

`messaging_api_url` di `cloudx.conf`. Dashboard: halaman Queues (Compute).

## Batasan

Tidak ada topik, subscription, webhook, notifikasi push, pesan tertunda, dead-letter, enkripsi pesan, atau kuota selain 10.000 per antrean. Pesan disimpan di PostgreSQL controller, jadi ini bukan broker throughput tinggi. Dashboard hanya membuat, melihat jumlah, dan menghapus antrean; kirim dan klaim lewat API atau CLI.
