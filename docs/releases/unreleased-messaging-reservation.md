# CloudX (version to be assigned) - Messaging and Reservation services

Two new API-only services on the controller, "not 1:1" with their OpenStack counterparts. Both are intentionally basic.

## Messaging (Zaqar-like, port 8022)
Per-project queues with post, peek, claim and delete (`cloudx queue ...`, Queues page). At-least-once: a message whose claim expires comes back. Claims use `FOR UPDATE SKIP LOCKED`. Limits: 64 KiB body, 10,000 messages per queue, message ttl 60 s to 14 days.

## Reservation (Blazar-like, port 8023)
Time-boxed flavor leases (`cloudx reservation ...`, Reservations page). A lease is refused (409) when overlapping leases of all projects would exceed the cluster's VCPU or RAM from Placement (503 if Placement has no inventory).

## Upgrading
1. Deploy `services/`, re-run `scripts/install-systemd.sh` (installs `cloudx-messaging-api` and `cloudx-reservation-api`), run `cloudx-db-migrate` for `messaging` and `reservation` (from `/tmp`).
2. Add `messaging_api_url` and `reservation_api_url` (see `etc/cloudx/cloudx.conf.sample`) to `/etc/cloudx/cloudx.conf`; append with a leading newline. Enable both units, redeploy the dashboard and the CLI.

## Known limitations
- **Reservation is not enforced:** normal instances can still use reserved capacity and an active lease creates nothing. Free capacity ignores VMs already running. Flavor leases only; no host reservation, extension or notification.
- **Messaging:** no topics, subscriptions, webhooks, delayed messages, dead-letter queues or encryption. Messages live in the controller's PostgreSQL, so it is not a high-throughput broker. The dashboard creates and deletes queues and shows counts; post and claim go through the API or CLI.
- Parallel-claim behaviour depends on PostgreSQL row locks; unit tests run on SQLite and do not cover it.
