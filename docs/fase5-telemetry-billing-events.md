# 📊 Phase 5 — Telemetry, Billing & Event Microservices

This document describes the architecture, API endpoints, and operation of the Phase 5 microservices: **Telemetry (Ceilometer/Prometheus-like)**, **Billing & Rating (CloudKitty-like)**, and **Events & Webhooks (Panko/Notification-like)**.

---

## 📋 Service Overview

| Service | Port | Database | Description |
|---------|------|----------|-----------|
| `telemetry-api` | 8010 | `cloudx` | Collection of host/VM observability metrics, Prometheus export (`/metrics`), statistics aggregation. |
| `telemetry-worker` | N/A | N/A | Daemon that periodically collects libvirt/OVN metrics. |
| `billing-api` | 8011 | `cloudx_billing` | Rating sheets, resource usage records, invoice generation, and PDF conversion. |
| `billing-worker` | N/A | `cloudx_billing` | Background cron that periodically calculates usage per tenant. |
| `event-api` | 8012 | `cloudx_events` | Internal cloud platform event emission, event type catalogue, and webhook subscriber engine. |
| `event-worker` | N/A | `cloudx_events` | Webhook delivery worker with exponential retry & HMAC signature. |

---

## 1. Telemetry API (`telemetry-api` - Port 8010)

Manages the collection of performance data (vCPU, memory, disk IOPS, network throughput) from compute nodes and hypervisors.

### Main API Endpoints
- `GET /telemetry/metric-types` — List of integrated metric types (cpu_percent, memory_used_mb, and so on).
- `POST /telemetry/metrics` — Push a single metric point per VM/host.
- `POST /telemetry/metrics/batch` — Ingest a batch of metrics at once.
- `GET /telemetry/metrics` — Query metric series filtered by `instance_id`, `metric_type`, `from_time`, `to_time`.
- `GET /telemetry/stats` — Average, min, max, count statistics aggregation per metric type.
- `GET /telemetry/instances/{instance_id}/stats` — Performance statistics for a specific VM.
- `GET /metrics` — Prometheus exposition format endpoint, to be scraped by a Prometheus scraper.
- `DELETE /telemetry/metrics/cleanup` — Maintenance cleanup of expired metrics (default retention: 7 days).

### CLI Usage
```bash
cloudx telemetry metric-type-list
cloudx telemetry metric-list --instance <vm_id> --type cpu_percent
cloudx telemetry stats
cloudx telemetry prometheus
```

---

## 2. Billing & Rating Engine (`billing-api` - Port 8011)

Service that calculates private cloud resource costs from usage duration (metering) and generates bills/invoices.

### Main API Endpoints
- `GET /billing/rating-sheets` & `POST /billing/rating-sheets` — Price catalogue per resource class (vCPU, RAM, Volume Storage, Floating IP).
- `GET /billing/usage-records` & `POST /billing/usage-records` — Actual usage records per project/tenant.
- `GET /billing/invoices` & `POST /billing/invoices` — Create monthly or custom-range invoices.
- `POST /billing/invoices/{id}/pdf` — Generate the invoice file (PDF/HTML template).
- `GET /billing/summary` — Summary of total consumption per tenant/project.

### CLI Usage
```bash
cloudx billing rating-sheet-list
cloudx billing rating-sheet-create --resource-type vcpu --unit vcpu-hour --rate-per-unit 100 --currency IDR
cloudx billing usage-list --project <project_id>
cloudx billing invoice-list
cloudx billing invoice-create --project <project_id>
cloudx billing invoice-pdf <invoice_id> --out invoice.pdf
cloudx billing summary
```

---

## 3. Event & Webhook Engine (`event-api` - Port 8012)

Catalogue of the internal platform's audit log events, plus a real-time webhook dispatcher for external integrations (Slack/PagerDuty/custom HTTP listeners).

### Main API Endpoints
- `POST /events/emit` — Emit a new event log entry (for example: `instance.created`, `volume.attached`).
- `GET /events/types` — List of event types registered in the system.
- `GET /events/logs` — Query the filtered event audit trail.
- `POST /events/subscriptions` & `GET /events/subscriptions` — Register webhook listener endpoints.
- `GET /events/deliveries` — Track webhook delivery status.
- `POST /events/deliveries/{id}/retry` — Retry a failed webhook delivery.

### CLI Usage
```bash
cloudx event type-list
cloudx event log-list --type instance.created
cloudx event subscription create "slack-notifier" --endpoint-url https://hooks.slack.com/services/xxx --event-type instance.created
cloudx event subscription list
cloudx event delivery-list
```
