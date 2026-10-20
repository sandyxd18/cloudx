# CloudX (version to be assigned) - networking IP allocation race

## Fixes
- networking-api: concurrent `POST /ports` on one subnet could pick the same free IP in different gunicorn workers; OVN then rejected the duplicate (HTTP 502) and left orphan logical switch ports. Allocation now takes a per-subnet PostgreSQL advisory lock until commit. 16 parallel creates: 2 succeeded before, 16 after.

## Known limitations
- Only allocation through `allocate_fixed_ip` is serialized; other code paths that choose addresses themselves were not audited.
- SQLite (tests) has no advisory lock, so the unit tests do not reproduce the race.
