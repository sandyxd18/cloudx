# Struktur Kode CloudX

Dokumen ini menjelaskan tata letak kode setelah refactor (service dipecah per tanggung jawab, `shared` menjadi paket,
skema database dikelola Alembic). Untuk arsitektur tingkat tinggi lihat `architecture.md`.

## Tata letak repo

```
cli/cloudx_cli/          CLI `cloudx` (Click)
  main.py                entry point (`cloudx_cli.main:cli`), hanya mengimpor modul perintah
  app.py                 grup root `cli`
  common.py              config/kredensial, token, `_api`, format output
  commands/*.py          satu modul per kelompok perintah (auth, compute, network, storage, ...)
services/
  shared/                paket `shared` (config, auth middleware, rpc, service_client, schema, migrate)
  migrations/            alat Alembic bersama (env.py, registry.py); riwayat ada di tiap service
  <service>/app/         kode service (lihat di bawah)
  <service>/migrations/  riwayat migrasi service itu
tests/                   pytest: satu folder per service (unit) + tests/live (butuh lab); lihat bagian Tes
etc/                     unit systemd dan contoh konfigurasi
scripts/                 installer dan skrip uji lab
```

## Service API yang sudah dipecah (auth, compute, block_store, networking)

Setiap `app/main.py` hanya membuat aplikasi FastAPI, middleware, event startup, health check, dan memasang router.

| Modul | Isi |
|---|---|
| `config.py` | konstanta dari environment |
| `db.py` | engine, `SessionLocal`, dependency `DB` |
| `models.py` | model SQLAlchemy |
| `schemas.py` | skema Pydantic |
| `clients.py` | HTTP/RPC ke service lain (compute, block_store); state `_rpc` ada di sini |
| `serializers.py`, `lookups.py`, `service.py` | konversi respons, pencarian entitas, logika bisnis bersama |
| `bootstrap.py` | data awal (flavor, quota, admin, SG default); **bukan** DDL |
| `routers/*.py` | endpoint, dikelompokkan per sumber daya |

Aturan yang perlu diingat:

- Nama yang di-patch oleh tes atau yang nilainya diganti saat runtime (misalnya `clients._rpc`, `clients.api_get`,
  `ovn_cli.ovn`) dipanggil lewat modulnya (`clients.api_get(...)`), bukan di-import dengan nama.
- Service lain (image, placement, billing, dan seterusnya) masih satu file `main.py`; pola yang sama dipakai kalau
  nanti dipecah.

## Skema database

Hanya Alembic yang membuat atau mengubah tabel; service gagal start kalau skema belum dimigrasi. Lihat
`services/migrations/README.md` (perintah: `sudo make migrate`, `cloudx-db-migrate --revision <svc> --autogenerate`).

## Tes

```
tests/
  conftest.py        env wajib, opsi --live, isolasi os.environ per tes, fixture `networking` dan `cloudx`
  stack.py           fresh_service(): impor ulang satu service di atas database sqlite sementara
  <service>/         tes unit per service (auth, compute, compute_worker, networking, block_store, image,
                     key_manager, k8s_engine, load_balancer, placement, metadata, cli, scripts, shared)
  live/              tes API terhadap lab yang sedang berjalan; hanya jalan dengan --live
```

- `pytest` (atau `make test`): hanya tes unit, tanpa lab, sekitar semenit. Inilah yang dijalankan CI
  (`.github/workflows/tests.yml`, bersama `make lint`). Dependensi: `pip install -r requirements-dev.txt`.
- `pytest --live tests/live` (atau `make test-live`): tes API terhadap lab. Butuh `AUTH_ADMIN_PASSWORD` dan
  `AUTH_SECRET_KEY` (lihat `/etc/cloudx/cloudx.env` di controller); URL service bisa diganti dengan
  `<SERVICE>_API_URL` (default `127.0.0.1`, misalnya lewat SSH tunnel). Di akhir sesi, `tests/live/cleanup.py`
  menghapus resource yang dibuat tes (router, network, keypair, resource provider palsu, dan seterusnya); bisa juga
  dijalankan manual.
- Tes service yang sudah dipecah memakai `fresh_service(...)` / fixture `networking`; tes `shared/` mencakup
  middleware auth, `shared.proc`, migrasi Alembic, dan penjaga penanganan error.
- Tambahkan tes baru di folder service yang bersangkutan. Tes yang butuh lab masuk `tests/live` dan harus
  membersihkan hasilnya (atau memakai pola nama yang dikenali `cleanup.py`).

## Penanganan error

- Jangan menelan error diam-diam. `except Exception:` harus mencatat log (`log.warning(...)`), mempersempit tipe
  exception-nya, atau, kalau memang best-effort, menyebut alasannya di baris `except` (`# best-effort: <alasan>`).
  `tests/test_error_handling.py` menggagalkan handler diam baru yang tidak punya alasan.
- Reconciler (block_store_worker, networking, image_worker) bertindak berdasarkan apa yang mereka amati. Pengecekan
  keberadaan yang gagal mengembalikan `None` (tidak diketahui) dan record dilewati; hanya `False` yang berarti
  "tidak ada".
- Event ke RabbitMQ dan pengayaan respons (nama image, floating IP, dan sebagainya) bersifat best-effort: kegagalannya
  dicatat sebagai warning/debug dan tidak menggagalkan request.

## Menjalankan perintah eksternal

Semua service menjalankan perintah (lvm, qemu-img, rbd, ovn-nbctl, virsh, mc, ...) lewat `shared.proc`, bukan
`subprocess`/`os.system` langsung (`tests/test_proc.py` menggagalkan pemakaian langsung):

- `run([...])` selalu menerima **list** (string ditolak, tidak ada shell), punya **timeout** (default 60 detik;
  `timeout=None` boleh, tapi harus ditulis eksplisit, misalnya untuk menyalin seluruh image), dan melempar
  `CommandError` (subclass `RuntimeError`) berisi exit code dan stderr. Binary yang tidak ada dan timeout dilaporkan
  dengan cara yang sama (exit 127 / 124).
- `succeeds([...])` untuk probe (`lvdisplay`, `ovs-vsctl br-exists`, ...): `True`/`False`, dan melempar `CommandError`
  kalau probe tidak bisa dijalankan. Hasil itu berarti "tidak diketahui", bukan "tidak ada".
- `secrets=[...]` menyamarkan nilai rahasia di log dan pesan error (pesan error ikut tersimpan di field API).
  Kredensial MinIO dikirim lewat environment (`MC_HOST_cloudx`), bukan argumen perintah.
- Pengecualian: shell interaktif dashboard (`asyncio.create_subprocess_exec` dengan PTY). CLI `cloudx` berdiri
  sendiri (tanpa `shared`), jadi memakai `subprocess.run` dengan list argumen dan memvalidasi nama service.
