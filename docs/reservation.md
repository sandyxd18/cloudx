# Reservation (setara Blazar)

Lease kapasitas flavor untuk satu jendela waktu. Lease ditolak bila cluster tidak muat menampungnya.

## Arsitektur

- `reservation-api` (controller, port 8023), tabel `rsv_leases`. Tanpa worker atau thread latar.
- Lease berisi flavor x `amount`; `vcpus` dan `ram_mb` dihitung saat dibuat dan disimpan, jadi flavor yang berubah kemudian tidak mengubah lease.
- Status (`pending`, `active`, `expired`) dihitung saat dibaca dari waktu sekarang.
- Admission: pada titik mana pun di jendela lease baru, total vCPU atau RAM seluruh lease yang tumpang tindih (semua project) tidak boleh melebihi kapasitas. Kapasitas = jumlah (total - reserved) x allocation_ratio untuk `VCPU` dan `MEMORY_MB` dari seluruh resource provider di Placement. Inventory kosong: 503, bukan angka tebakan. Di PostgreSQL, admission memakai advisory lock supaya dua permintaan bersamaan tidak sama-sama lolos.
- Batas: jendela maks 30 hari, `start_at` tidak di masa lalu, `amount` 1 sampai 100. Waktu tanpa offset dianggap UTC.

## API

```
POST   /leases   {"name","flavor","amount","start_at","end_at"}      GET /leases   GET /leases/{id|name}
DELETE /leases/{id|name}
GET    /capacity?start=&end=      (total, puncak yang dipesan, sisa)
```

## CLI

```
cloudx reservation create weekend --flavor small --amount 3 --start 2026-11-01T08:00 --end 2026-11-03T08:00
cloudx reservation list | show | delete | capacity
```

## Konfigurasi

`reservation_api_url`, `compute_api_url`, `placement_api_url` di `cloudx.conf`. Dashboard: halaman Reservations (Compute).

## Batasan

**Lease belum diberlakukan terhadap compute.** VM biasa tetap bisa memakai kapasitas yang sudah dipesan, dan lease aktif tidak membuat VM. Ia buku catatan kapasitas dengan penolakan bentrok antar lease. Hanya jenis flavor; tidak ada reservasi host fisik, perpanjangan lease, atau notifikasi. Kapasitas dibaca dari Placement tanpa memperhitungkan VM yang sudah berjalan, jadi sisa kapasitas sebenarnya bisa lebih kecil. Langkah berikutnya (belum dikerjakan): buat allocation di Placement saat lease mulai, dan izinkan `create server` menyebut lease.
