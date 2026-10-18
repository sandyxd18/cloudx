# DNS (setara Designate, berbasis BIND9)

Layanan DNS CloudX: zona dan record milik pengguna, plus nama otomatis untuk resource platform. Setara Designate dalam fitur, bukan 1:1 (lihat batasan).

## Komponen (controller saja)

| Unit | Fungsi |
|---|---|
| `cloudx-dns-api` | API (port 8016), menyimpan zona/record di PostgreSQL |
| `cloudx-dns-worker` | membuat file zona dan `named.conf`, reload BIND, menyapu nama otomatis tiap 60 detik |
| `cloudx-named-auth` | BIND otoritatif, tanpa rekursi, `172.20.0.120` |
| `cloudx-named-resolver` | BIND rekursif untuk tenant: zona internal + forwarder, `172.20.0.118` |

File BIND ada di `/var/lib/cloudx/dns/{auth,resolver}`. BIND tidak boleh bind ke `0.0.0.0` karena `systemd-resolved` memakai port 53 di localhost.

## Konfigurasi (`/etc/cloudx/cloudx.conf`)

`dns_platform_domain`, `dns_public_address`, `dns_resolver_address`, `dns_upstream`, `dns_resolver_clients`. Contoh ada di `etc/cloudx/cloudx.conf.sample`.

`dns_resolver_clients` harus memuat jaringan node compute/gateway: lalu lintas VM di-SNAT ke alamat node (terbukti di lab, sumbernya `172.20.0.117`, bukan IP VM).

## Zona dan record

```
cloudx dns zone create example.com
cloudx dns zone show example.com          # menampilkan NS yang diisi di registrar
cloudx dns record create example.com www A 192.0.2.10
cloudx dns record list example.com
cloudx dns zone create corp.internal --internal
cloudx dns secondary add example.com 203.0.113.5
```

- Tipe: A, AAAA, CNAME, MX, TXT, SRV, NS, PTR. SOA dan NS di apex dikelola sistem.
- Aturan CNAME ditegakkan (tidak boleh berbagi nama dengan record lain, tidak boleh di apex).
- Nama di bawah `cloud.lab` dicadangkan untuk platform.
- Status zona `PENDING` sampai worker memuatnya, lalu `ACTIVE`.
- Kuota per project: `max_zones` (10) dan `max_recordsets` (200); record otomatis tidak dihitung.

## Nama otomatis

| Resource | Nama | Dilayani |
|---|---|---|
| Floating IP | `<ip-dash>.compute.cloud.lab` | auth + resolver |
| Load balancer + floating IP | `<nama>-<6 id>.elb.cloud.lab` | auth + resolver |
| Bucket | `<bucket>-<8 id project>.s3.cloud.lab` | auth + resolver |
| Server dan VIP LB | `<nama>.<project>.internal.cloud.lab` | resolver saja |

Record dibuat layanan pemilik saat create/delete dan direkonsiliasi oleh sapuan. Kegagalan DNS tidak menggagalkan pembuatan resource. Record otomatis hanya-baca (409).

Subnet baru tanpa `dns_nameservers` memakai resolver platform.

## Secondary

Zona publik bisa didaftarkan secondary berdasarkan IP: server itu boleh AXFR dan menerima NOTIFY. CloudX hanya primary. Alamat loopback, unspecified, dan multicast ditolak.

## Batasan

Tanpa TSIG, DNSSEC, zona bertipe secondary, impor zona, PTR floating IP, pool/blacklist. Resolver `dnssec-validation no`. Detail lengkap di `docs/releases/v0.8.0.md`.

## Uji

`scripts/test-dns.sh` belum ada; verifikasi lab dilakukan manual (dig ke `.120` dan `.118`, AXFR dari secondary terdaftar, VM nyata untuk nama internal).
