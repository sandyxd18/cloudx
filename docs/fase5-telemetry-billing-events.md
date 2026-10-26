# Phase 5: Telemetry, Billing & Event

This document describes the three CloudX observability services: **Telemetry** (VM metrics), **Billing** (rating, usage, invoices) and **Event** (event log + webhooks).

## Service overview

| Service | Port | Database | Purpose |
|---------|------|----------|---------|
| `telemetry-api` | 8010 | `cloudx` | Stores and serves per-VM metrics, and the Prometheus endpoint `/metrics`. |
| `telemetry-worker` | - | - | Collects libvirt metrics on the compute node, sends them to `telemetry-api` (in batches), and triggers retention cleanup. |
| `billing-api` | 8011 | `cloudx_billing` | Rating sheets, usage records, invoices, PDF/HTML. |
| `billing-worker` | - | - | Fetches resource data from the other APIs, computes usage per interval, and posts it to `/billing/usage-records/batch`. |
| `event-api` | 8012 | `cloudx_events` | RabbitMQ consumer, event log, webhook subscriptions, delivery queue. |
| `event-worker` | - | - | Sends webhooks (HMAC when there is a secret), retries with backoff. |

## Event architecture

```
other service ──publish──> RabbitMQ (fanout) ──> event-api (EventConsumer)
                                                   │ store EventLog
                                                   │ create WebhookDelivery (pending)
                                                   │   for matching project subscriptions
                                                   ▼
                                  event-worker ──> GET deliveries?status=pending
                                                   ──> POST to endpoint_url
                                                   ──> PATCH /events/deliveries/{id}
```

- `event-api` starts the RabbitMQ consumer at startup (daemon thread). Every bus message is stored as an event; `project_id` is taken from the payload. An event without `project_id` is stored but produces no delivery.
- `POST /events/emit` uses the same storage path (event + delivery).
- A delivery is only created for active subscriptions in the same project whose `event_types` contain that event type or `*`.
- The worker uses admin endpoints (`PATCH /events/deliveries/{id}`, `GET /events/internal/subscriptions/{id}`) to update status and read the secret.

## Scoping rules

All services read `project_id` and `role` from the token.

- **Admin**: may see every project, or filter with `?project_id=`.
- **Non-admin**: always limited to the token's own project. `?project_id=` is ignored. Objects of another project (invoice, subscription, delivery) give **404**.
- **Admin only**:
  - Telemetry: `POST /telemetry/metrics`, `POST /telemetry/metrics/batch`, `DELETE /telemetry/metrics/cleanup`.
  - Billing: `POST/PATCH /billing/rating-sheets`, `POST /billing/usage-records`, `POST /billing/usage-records/batch`, `POST /billing/cleanup`.
  - Event: `PATCH /events/deliveries/{id}`, `GET /events/internal/subscriptions/{id}`.
- `/health`, `/events/types` and `/metrics` (telemetry) do not need a token.

## 1. Telemetry API (port 8010)

| Endpoint | Notes |
|----------|-------|
| `GET /telemetry/metric-types` | List of metric types (`cpu_percent`, `memory_used_mb`, `memory_total_mb`, `disk_read_bytes`, `disk_write_bytes`, `net_rx_bytes`, `net_tx_bytes`). |
| `POST /telemetry/metrics` | Ingest one point (admin). |
| `POST /telemetry/metrics/batch` | Ingest many points (admin), replies `{"ingested": n}`. |
| `GET /telemetry/metrics` | Filters `instance_id`, `project_id` (admin), `metric_type`, `since`, `until`, `limit` (1-1000, default 100), `offset`. Newest first. |
| `GET /telemetry/stats` | An **array** per `(metric_type, instance_id)`: `unit`, `count`, `avg`, `min`, `max`, `last`, `first_ts`, `last_ts`. Parameters `metric_type`, `instance_id`, `project_id`, `hours` (default 1). |
| `GET /telemetry/series` | Time series: `metric_type` and `instance_id` are required; `hours`, `granularity` = `raw`/`minute`/`hour`/`day`. Items: `timestamp`, `avg`, `min`, `max`, `count`. |
| `GET /telemetry/instances/{id}/stats` | Summary per metric type for one VM; 404 when there is no data (or it belongs to another project). |
| `GET /metrics` | Prometheus format, data of the last 5 minutes. **Open, no authentication.** |
| `DELETE /telemetry/metrics/cleanup` | Delete metrics older than the retention (admin). |

CLI:
```bash
cloudx telemetry metric-type-list
cloudx telemetry metric-list --instance <vm_id> --type cpu_percent
cloudx telemetry stats
cloudx telemetry server-stats <vm_id>
cloudx telemetry prometheus
```

## 2. Billing API (port 8011)

| Endpoint | Notes |
|----------|-------|
| `GET /billing/rating-sheets`, `GET /billing/rating-sheets/{id}` | Price catalog (default: vcpu, ram, volume, floating_ip, load_balancer). |
| `POST /billing/rating-sheets` (admin) | Create a rating sheet. |
| `PATCH /billing/rating-sheets/{id}` (admin) | **Partial** update: only the fields that are sent change. |
| `POST /billing/usage-records` (admin) | One usage record with an explicit rate. |
| `POST /billing/usage-records/batch` (admin) | Many records; the rate comes from the active rating sheet. Replies `{"created": n, "skipped": [{"index", "reason"}]}`; reasons are `no active rating sheet` or `duplicate` (key: project, resource_type, resource_id, period_start, period_end). |
| `GET /billing/usage-records` | Filters `project_id`, `resource_type`, `period_start`, `period_end`, `limit`, `offset`. |
| `POST /billing/cleanup?days=365` (admin) | Delete usage records whose `period_end` is older than `days`. Replies `{"deleted": n}`. |
| `POST /billing/invoices` | **Query** parameters: `project_id`, `year` + `month` (UTC calendar month), or `period_start`/`period_end`, `notes`. A duplicate invoice (same project + period) gives **409**. A month outside 1-12 gives 422. Tax 11%, status `draft`. |
| `GET /billing/invoices`, `GET /billing/invoices/{id}` | List and detail (detail includes `items`). |
| `POST /billing/invoices/{id}/pdf` | Base64 PDF when WeasyPrint is installed, HTML otherwise. |
| `GET /billing/summary[/{project_id}]` | Totals per resource kind, `total_cost`, and `by_resource` (`resource_type`, `quantity`, `cost`). |

CLI:
```bash
cloudx billing rating-sheet-list
cloudx billing rating-sheet-show <id>
cloudx billing rating-sheet-update <id> --rate-per-unit 120 --inactive
cloudx billing usage-list --project <project_id>
cloudx billing invoice-create --project <project_id> --year 2026 --month 9
cloudx billing invoice-list
cloudx billing invoice-show <id>        # including line items
cloudx billing invoice-pdf <id> --out invoice.pdf
cloudx billing summary [project_id]
```

## 3. Event API (port 8012)

| Endpoint | Notes |
|----------|-------|
| `POST /events/emit` | Store an event. Non-admin: `project_id`/`user_id` are forced from the token. |
| `POST /events/deliver` | Same as emit, replies 202 `{"event_id", "status": "queued"}`. |
| `GET /events/types` | Event type catalog (no token). |
| `GET /events/logs` | Filters `event_type`, `resource_type`, `resource_id`, `project_id` (admin), `since`, `until`, `limit`, `offset`. |
| `POST /events/subscriptions` | The project comes from the token. The URL is checked by the SSRF guard. |
| `GET /events/subscriptions[/{id}]`, `DELETE /events/subscriptions/{id}` | Only the caller's own project's subscriptions (admin: all). |
| `GET /events/internal/subscriptions/{id}` (admin) | Includes `secret`; used by event-worker. |
| `GET /events/deliveries` | Filters `subscription_id`, `status`. With `status=pending`, only those whose `next_retry_at` is empty or already past. |
| `PATCH /events/deliveries/{id}` (admin) | Change `status`, `response_code`, `attempts`, `next_retry_at`, `last_error`. |
| `POST /events/deliveries/{id}/retry` | Reset: `status=pending`, `attempts=0`, `next_retry_at=null`. |

- The **subscription secret** only appears in the reply to `POST /events/subscriptions`. GET/list only return `has_secret` (bool).
- A **delivery** has `status` (`pending` / `success` / `failed`), `response_code`, `attempts` (alias `attempt_count`), `event_type`, `payload`, `next_retry_at`, `last_error`.
- **SSRF guard**: the URL must be `http(s)` and its host must resolve to a public address. Loopback, private, link-local, reserved, multicast and unspecified addresses are rejected (422). Turn the guard off for a lab only with `EVENT_ALLOW_PRIVATE_WEBHOOKS=true`.

CLI:
```bash
cloudx event type-list
cloudx event log-list --type instance.created
cloudx event subscription create "notifier" --endpoint-url https://example.com/hook --event-type instance.created --secret xxx
cloudx event subscription list
cloudx event delivery-list
cloudx event delivery-retry <id>
```

## Environment variables

| Variable | Service | Notes |
|----------|---------|-------|
| `CORS_ORIGINS` | telemetry-api, event-api | Comma-separated list of origins (default `http://localhost`); `*` is ignored. billing-api currently still allows all origins. |
| `EVENT_ALLOW_PRIVATE_WEBHOOKS` | event-api | `true` turns off the SSRF guard (lab only). |
| `RABBITMQ_URL` | event-api | Broker for the event consumer. |
| `TELEMETRY_RETENTION_DAYS` | telemetry-worker | Metric retention (default 7); the worker calls `DELETE /telemetry/metrics/cleanup?days=...`. |
| `METRICS_RETENTION_DAYS` | telemetry-api | Default retention of the cleanup endpoint when `days` is not given (default 7). |
| `DATABASE_URL`, `BILLING_DATABASE_URL`, `EVENT_DATABASE_URL` | each API | Database connection. |

## Worker systemd units

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

## Known limitations

- **Billing cannot count a resource deleted in the middle of an interval.** The resource APIs do not return `deleted_at`, so the worker only sees resources that still exist when it polls; usage before the deletion within that interval is lost.
- **Prometheus `/metrics` is open** without a token; restrict it with a firewall/HAProxy.
- billing-api does not restrict CORS yet (`allow_origins=["*"]`).
- An event without `project_id` in the payload is stored but not sent to webhooks.
- The payload of an event from `POST /events/emit` given as a dict is stored as `str(dict)`, not JSON; events from the bus are stored as JSON.
