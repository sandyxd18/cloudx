# Reservation (Blazar-like)

Flavor capacity leases for a time window. A lease is rejected when the cluster cannot hold it.

## Architecture

- `reservation-api` (controller, port 8023), table `rsv_leases`. No worker or background thread.
- A lease is flavor x `amount`; `vcpus` and `ram_mb` are computed at creation and stored, so a flavor that changes later does not change the lease.
- The status (`pending`, `active`, `expired`) is computed at read time from the current time.
- Admission: at any point in the new lease's window, the total vCPU or RAM of all overlapping leases (all projects) must not exceed capacity. Capacity = the sum of (total - reserved) x allocation_ratio for `VCPU` and `MEMORY_MB` over all resource providers in Placement. Empty inventory: 503, not a guessed number. On PostgreSQL, admission uses an advisory lock so two simultaneous requests cannot both pass.
- Limits: window at most 30 days, `start_at` not in the past, `amount` 1 to 100. A time without an offset is taken as UTC.

## API

```
POST   /leases   {"name","flavor","amount","start_at","end_at"}      GET /leases   GET /leases/{id|name}
DELETE /leases/{id|name}
GET    /capacity?start=&end=      (total, peak reserved, remaining)
```

## CLI

```
cloudx reservation create weekend --flavor small --amount 3 --start 2026-11-01T08:00 --end 2026-11-03T08:00
cloudx reservation list | show | delete | capacity
```

## Configuration

`reservation_api_url`, `compute_api_url`, `placement_api_url` in `cloudx.conf`. Dashboard: the Reservations page (Compute).

## Limits

**Leases are not yet enforced against compute.** Ordinary VMs can still use capacity that has been reserved, and an active lease does not create a VM. It is a capacity ledger that rejects conflicts between leases. Flavor type only; no physical host reservation, lease extension or notifications. Capacity is read from Placement without counting VMs that are already running, so the real remaining capacity can be smaller. Next step (not done): create an allocation in Placement when a lease starts, and let `create server` name a lease.
