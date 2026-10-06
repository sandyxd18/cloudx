# Fase 5 — Telemetry, Billing & Event

Dokumen ini menjelaskan tiga layanan observabilitas CloudX: **Telemetry** (metrik VM), **Billing** (rating, usage, invoice), dan **Event** (event log + webhook).

## Ringkasan layanan

| Layanan | Port | Database | Fungsi |
|---------|------|----------|--------|
| `telemetry-api` | 8010 | `cloudx` | Menyimpan dan menyajikan metrik per VM, endpoint Prometheus `/metrics`. |
| `telemetry-worker` | - | - | Mengumpulkan metrik libvirt di compute node, mengirim ke `telemetry-api` (batch), memicu cleanup retensi. |
| `billing-api` | 8011 | `cloudx_billing` | Rating sheet, usage record, invoice, PDF/HTML. |
| `billing-worker` | - | - | Mengambil data resource dari API lain, menghitung usage per interval, mengirim ke `/billing/usage-records/batch`. |
| `event-api` | 8012 | `cloudx_events` | Consumer RabbitMQ, event log, subscription webhook, antrean delivery. |
| `event-worker` | - | - | Mengirim webhook (HMAC bila ada secret), retry dengan backoff. |

## Arsitektur event

```
service lain ──publish──> RabbitMQ (fanout) ──> event-api (EventConsumer)
                                                   │ simpan EventLog
                                                   │ buat WebhookDelivery (pending)
                                                   │   untuk subscription project yang cocok
                                                   ▼
                                  event-worker ──> GET deliveries?status=pending
                                                   ──> POST ke endpoint_url
                                                   ──> PATCH /events/deliveries/{id}
```

- `event-api` memulai consumer RabbitMQ saat startup (thread daemon). Setiap pesan bus disimpan sebagai event; `project_id` diambil dari payload. Event tanpa `project_id` tersimpan tetapi tidak menghasilkan delivery.
- `POST /events/emit` juga memakai jalur penyimpanan yang sama (event + delivery).
- Delivery dibuat hanya untuk subscription aktif di project yang sama, dengan `event_types` berisi tipe event itu atau `*`.
- Worker memakai endpoint admin (`PATCH /events/deliveries/{id}`, `GET /events/internal/subscriptions/{id}`) untuk memperbarui status dan membaca secret.

## Aturan scoping

Semua layanan membaca `project_id` dan `role` dari token.

- **Admin**: boleh melihat semua project, atau menyaring dengan `?project_id=`.
- **Non-admin**: selalu dibatasi ke project tokennya sendiri. `?project_id=` diabaikan. Objek milik project lain (invoice, subscription, delivery) menghasilkan **404**.
- **Hanya admin**:
  - Telemetry: `POST /telemetry/metrics`, `POST /telemetry/metrics/batch`, `DELETE /telemetry/metrics/cleanup`.
  - Billing: `POST/PATCH /billing/rating-sheets`, `POST /billing/usage-records`, `POST /billing/usage-records/batch`, `POST /billing/cleanup`.
  - Event: `PATCH /events/deliveries/{id}`, `GET /events/internal/subscriptions/{id}`.
- `/health`, `/events/types` dan `/metrics` (telemetry) tidak memerlukan token.

## 1. Telemetry API (port 8010)

| Endpoint | Keterangan |
|----------|-----------|
| `GET /telemetry/metric-types` | Daftar tipe metrik (`cpu_percent`, `memory_used_mb`, `memory_total_mb`, `disk_read_bytes`, `disk_write_bytes`, `net_rx_bytes`, `net_tx_bytes`). |
| `POST /telemetry/metrics` | Ingest satu titik (admin). |
| `POST /telemetry/metrics/batch` | Ingest banyak titik (admin), balasan `{"ingested": n}`. |
| `GET /telemetry/metrics` | Filter `instance_id`, `project_id` (admin), `metric_type`, `since`, `until`, `limit` (1-1000, default 100), `offset`. Urutan terbaru dulu. |
| `GET /telemetry/stats` | **Array** per `(metric_type, instance_id)`: `unit`, `count`, `avg`, `min`, `max`, `last`, `first_ts`, `last_ts`. Parameter `metric_type`, `instance_id`, `project_id`, `hours` (default 1). |
| `GET /telemetry/series` | Deret waktu: wajib `metric_type` dan `instance_id`; `hours`, `granularity` = `raw`/`minute`/`hour`/`day`. Item: `timestamp`, `avg`, `min`, `max`, `count`. |
| `GET /telemetry/instances/{id}/stats` | Ringkasan per tipe metrik untuk satu VM; 404 bila tidak ada data (atau milik project lain). |
| `GET /metrics` | Format Prometheus, data 5 menit terakhir. **Terbuka tanpa autentikasi.** |
| `DELETE /telemetry/metrics/cleanup` | Hapus metrik lebih lama dari retensi (admin). |

CLI:
```bash
cloudx telemetry metric-type-list
cloudx telemetry metric-list --instance <vm_id> --type cpu_percent
cloudx telemetry stats
cloudx telemetry server-stats <vm_id>
cloudx telemetry prometheus
```

## 2. Billing API (port 8011)

| Endpoint | Keterangan |
|----------|-----------|
| `GET /billing/rating-sheets`, `GET /billing/rating-sheets/{id}` | Katalog harga (default: vcpu, ram, volume, floating_ip, load_balancer). |
| `POST /billing/rating-sheets` (admin) | Buat rating sheet. |
| `PATCH /billing/rating-sheets/{id}` (admin) | Update **parsial**: hanya field yang dikirim yang berubah. |
| `POST /billing/usage-records` (admin) | Satu usage record dengan rate eksplisit. |
| `POST /billing/usage-records/batch` (admin) | Banyak record; rate diambil dari rating sheet aktif. Balasan `{"created": n, "skipped": [{"index", "reason"}]}`; alasan `no active rating sheet` atau `duplicate` (kunci: project, resource_type, resource_id, period_start, period_end). |
| `GET /billing/usage-records` | Filter `project_id`, `resource_type`, `period_start`, `period_end`, `limit`, `offset`. |
| `POST /billing/cleanup?days=365` (admin) | Hapus usage record yang `period_end`-nya lebih lama dari `days`. Balasan `{"deleted": n}`. |
| `POST /billing/invoices` | Parameter **query**: `project_id`, `year` + `month` (bulan kalender UTC), atau `period_start`/`period_end`, `notes`. Invoice duplikat (project + periode sama) → **409**. Bulan di luar 1-12 → 422. Pajak 11%, status `draft`. |
| `GET /billing/invoices`, `GET /billing/invoices/{id}` | Daftar dan detail (detail memuat `items`). |
| `POST /billing/invoices/{id}/pdf` | PDF base64 bila WeasyPrint terpasang, selain itu HTML. |
| `GET /billing/summary[/{project_id}]` | Total per jenis resource, `total_cost`, dan `by_resource` (`resource_type`, `quantity`, `cost`). |

CLI:
```bash
cloudx billing rating-sheet-list
cloudx billing rating-sheet-show <id>
cloudx billing rating-sheet-update <id> --rate-per-unit 120 --inactive
cloudx billing usage-list --project <project_id>
cloudx billing invoice-create --project <project_id> --year 2026 --month 9
cloudx billing invoice-list
cloudx billing invoice-show <id>        # termasuk line items
cloudx billing invoice-pdf <id> --out invoice.pdf
cloudx billing summary [project_id]
```

## 3. Event API (port 8012)

| Endpoint | Keterangan |
|----------|-----------|
| `POST /events/emit` | Simpan event. Non-admin: `project_id`/`user_id` dipaksa dari token. |
| `POST /events/deliver` | Sama dengan emit, balasan 202 `{"event_id", "status": "queued"}`. |
| `GET /events/types` | Katalog tipe event (tanpa token). |
| `GET /events/logs` | Filter `event_type`, `resource_type`, `resource_id`, `project_id` (admin), `since`, `until`, `limit`, `offset`. |
| `POST /events/subscriptions` | Project diambil dari token. URL diperiksa guard SSRF. |
| `GET /events/subscriptions[/{id}]`, `DELETE /events/subscriptions/{id}` | Hanya subscription project sendiri (admin: semua). |
| `GET /events/internal/subscriptions/{id}` (admin) | Termasuk `secret`; dipakai event-worker. |
| `GET /events/deliveries` | Filter `subscription_id`, `status`. Dengan `status=pending`, hanya yang `next_retry_at` kosong atau sudah lewat. |
| `PATCH /events/deliveries/{id}` (admin) | Ubah `status`, `response_code`, `attempts`, `next_retry_at`, `last_error`. |
| `POST /events/deliveries/{id}/retry` | Reset: `status=pending`, `attempts=0`, `next_retry_at=null`. |

- **Secret subscription** hanya tampil di balasan `POST /events/subscriptions`. GET/list hanya mengembalikan `has_secret` (bool).
- **Delivery** berisi `status` (`pending` / `success` / `failed`), `response_code`, `attempts` (alias `attempt_count`), `event_type`, `payload`, `next_retry_at`, `last_error`.
- **Guard SSRF**: URL harus `http(s)` dan hostnya harus ter-resolve ke alamat publik. Loopback, private, link-local, reserved, multicast, dan unspecified ditolak (422). Matikan guard hanya untuk lab dengan `EVENT_ALLOW_PRIVATE_WEBHOOKS=true`.

CLI:
```bash
cloudx event type-list
cloudx event log-list --type instance.created
cloudx event subscription create "notifier" --endpoint-url https://example.com/hook --event-type instance.created --secret xxx
cloudx event subscription list
cloudx event delivery-list
cloudx event delivery-retry <id>
```

## Variabel lingkungan

| Variabel | Layanan | Keterangan |
|----------|---------|-----------|
| `CORS_ORIGINS` | telemetry-api, event-api | Daftar origin dipisah koma (default `http://localhost`); `*` diabaikan. billing-api saat ini masih mengizinkan semua origin. |
| `EVENT_ALLOW_PRIVATE_WEBHOOKS` | event-api | `true` mematikan guard SSRF (hanya lab). |
| `RABBITMQ_URL` | event-api | Broker untuk consumer event. |
| `TELEMETRY_RETENTION_DAYS` | telemetry-worker | Retensi metrik (default 7); worker memanggil `DELETE /telemetry/metrics/cleanup?days=...`. |
| `METRICS_RETENTION_DAYS` | telemetry-api | Retensi default endpoint cleanup bila `days` tidak diberikan (default 7). |
| `DATABASE_URL`, `BILLING_DATABASE_URL`, `EVENT_DATABASE_URL` | masing-masing API | Koneksi database. |

## Unit systemd worker

| Unit | Host |
|------|------|
| `cloudx-billing-worker` | controller |
| `cloudx-event-worker` | controller |
| `cloudx-telemetry-worker` | compute node |

```bash
sudo systemctl enable --now cloudx-billing-worker cloudx-event-worker     # controller
sudo systemctl enable --now cloudx-telemetry-worker                       # compute
journalctl -u cloudx-event-worker -f
```

## Batasan yang diketahui

- **Billing tidak bisa menghitung resource yang dihapus di tengah interval.** API resource tidak mengembalikan `deleted_at`, jadi worker hanya melihat resource yang masih ada saat polling; pemakaian sebelum penghapusan dalam interval itu hilang.
- **`/metrics` Prometheus terbuka** tanpa token; batasi lewat firewall/HAProxy.
- billing-api belum membatasi CORS (`allow_origins=["*"]`).
- Event tanpa `project_id` di payload disimpan tetapi tidak dikirim ke webhook.
- Payload event hasil `POST /events/emit` dengan dict disimpan sebagai `str(dict)`, bukan JSON; event dari bus disimpan sebagai JSON.
