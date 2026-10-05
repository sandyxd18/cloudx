# 📊 Phase 5 — Telemetry, Billing & Event Microservices

Dokumen ini menjelaskan arsitektur, API endpoints, serta operasional dari microservice Fase 5: **Telemetry (Ceilometer/Prometheus-like)**, **Billing & Rating (CloudKitty-like)**, dan **Event & Webhooks (Panko/Notification-like)**.

---

## 📋 Service Overview

| Service | Port | Database | Deskripsi |
|---------|------|----------|-----------|
| `telemetry-api` | 8010 | `cloudx` | Pengumpulan metrik observability host/VM, Prometheus export (`/metrics`), agregasi statistik. |
| `telemetry-worker` | N/A | N/A | Daemon pengumpul metrik libvirt/OVN periodik. |
| `billing-api` | 8011 | `cloudx_billing` | Rating sheet, pencatatan konsumsi resource (usage records), generasi invoice, dan konversi PDF. |
| `billing-worker` | N/A | `cloudx_billing` | Cron background perhitung penggunaan berkala per tenant. |
| `event-api` | 8012 | `cloudx_events` | Emisi event internal cloud platform, katalog event types, dan engine webhook subscriber. |
| `event-worker` | N/A | `cloudx_events` | Webhook delivery worker dengan eksponensial retry & HMAC signature. |

---

## 1. Telemetry API (`telemetry-api` - Port 8010)

Mengelola koleksi data performa (vCPU, memory, disk IOPS, network throughput) dari compute node dan hypervisor.

### API Endpoints Utama
- `GET /telemetry/metric-types` — Daftar tipe metrik yang terintegrasi (cpu_percent, memory_used_mb, dll.).
- `POST /telemetry/metrics` — Push single metric point per VM/host.
- `POST /telemetry/metrics/batch` — Ingest batch metrik sekaligus.
- `GET /telemetry/metrics` — Query series metrik terfilter berdasarkan `instance_id`, `metric_type`, `from_time`, `to_time`.
- `GET /telemetry/stats` — Agregasi statistik rata-rata, min, max, count per jenis metrik.
- `GET /telemetry/instances/{instance_id}/stats` — Statistik performa VM spesifik.
- `GET /metrics` — Prometheus Exposition Format endpoint untuk dikikis oleh Prometheus scraper.
- `DELETE /telemetry/metrics/cleanup` — Maintenance cleanup metrik kadaluarsa (retensi bawaan: 7 hari).

### CLI Usage
```bash
cloudx telemetry metric-type-list
cloudx telemetry metric-list --instance <vm_id> --type cpu_percent
cloudx telemetry stats
cloudx telemetry prometheus
```

---

## 2. Billing & Rating Engine (`billing-api` - Port 8011)

Layanan perhitungan biaya resource private cloud berbasis durasi penggunaan (metering) dan generasi tagihan/invoice.

### API Endpoints Utama
- `GET /billing/rating-sheets` & `POST /billing/rating-sheets` — Katalog tarif per kelas resource (vCPU, RAM, Volume Storage, Floating IP).
- `GET /billing/usage-records` & `POST /billing/usage-records` — Catatan penggunaan aktual per project/tenant.
- `GET /billing/invoices` & `POST /billing/invoices` — Pembuatan invoice bulanan / kustom rentang waktu.
- `POST /billing/invoices/{id}/pdf` — Generasi file tagihan (PDF/HTML template).
- `GET /billing/summary` — Ringkasan konsumsi total per tenant/project.

### CLI Usage
```bash
cloudx billing rating-sheet-list
cloudx billing rating-sheet-create --resource-type vcpu --unit vcpu-hour --rate-per-unit 100 --currency IDR
cloudx billing usage-list --project <project_id>
cloudx billing invoice-list
cloudx billing invoice-create --project <project_id>
cloudx billing invoice-pdf <invoice_id> --out tagihan.pdf
cloudx billing summary
```

---

## 3. Event & Webhook Engine (`event-api` - Port 8012)

Katalog audit log event platform internal serta dispatcher webhook waktu nyata untuk integrasi eksternal (Slack/PagerDuty/custom HTTP listener).

### API Endpoints Utama
- `POST /events/emit` — Emisi log event baru (misal: `instance.created`, `volume.attached`).
- `GET /events/types` — Daftar tipe event terdaftar pada sistem.
- `GET /events/logs` — Query audit trail event terfilter.
- `POST /events/subscriptions` & `GET /events/subscriptions` — Pendaftaran endpoint webhook listener.
- `GET /events/deliveries` — Tracking status pengiriman webhook.
- `POST /events/deliveries/{id}/retry` — Percobaan pengiriman ulang webhook gagal.

### CLI Usage
```bash
cloudx event type-list
cloudx event log-list --type instance.created
cloudx event subscription create "slack-notifier" --endpoint-url https://hooks.slack.com/services/xxx --event-type instance.created
cloudx event subscription list
cloudx event delivery-list
```
