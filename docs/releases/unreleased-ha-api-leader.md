# CloudX (version to be assigned) - two ha-api instances with a leader lock

Optional; a single ha-api keeps working with no changes.

## Changes
- ha-api: the monitor loop runs only on the instance holding a PostgreSQL session advisory lock (`pg_try_advisory_lock`). The lock disappears with the process or connection; another instance takes it within about 10 s and gives every host a fresh 60 s before judging it. All API endpoints stay active on every instance.
- ha-api no longer resets every host's `last_seen` on plain startup (only when it takes the leader lock), so a restarting follower cannot delay detection.
- `ha_api_url` may list several instances (comma-separated). compute-worker heartbeats to the first one that answers; ha-watchdog treats ha-api as reachable if any instance answers.

## Upgrading
Nothing is required for a single instance. For a second instance see `docs/ha.md`: a `database_url` and the other service URLs in `/etc/cloudx/ha-api/ha-api.conf`, a `pg_hba.conf` line plus firewall rule for that node, and the comma-separated `ha_api_url` on compute nodes.

## Known limitations
- This is not controller HA: PostgreSQL, compute-api, block-store-api and auth stay on the controller. If the controller is down the second instance is alive but cannot evacuate anything.
- A PostgreSQL connection that dies without closing (network partition) frees the lock only when the server notices; no new leader until then.
- Failover takes up to one poll (10 s) plus the 60 s grace for hosts.
- The dashboard knows a single ha-api URL. No leader priority: whichever instance gets the lock first wins.
- PostgreSQL must accept connections from the second node, which widens the database's network exposure to that one address.
