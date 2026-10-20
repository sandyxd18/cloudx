# CloudX (version to be assigned) - HA watchdog

Self-fencing for Instance HA. There are **upgrade steps**.

## Upgrading
1. Run `cloudx-db-migrate ha` (`ha_0002`, adds `ha_hosts.watchdog_at`) and redeploy `ha-api` and `compute-worker`.
2. On every compute node create `/etc/cloudx/ha-watchdog/ha-watchdog.conf` (`[DEFAULT]`) with `ha_watchdog_host` (node name), `ha_watchdog_peers` (URLs of the other compute nodes, port 8021) and, if needed, `ha_watchdog_timeout`; then enable `cloudx-ha-watchdog`. Port 8021 must be reachable between compute nodes.

## Changes
- New `cloudx-ha-watchdog` (compute nodes). When ha-api is unreachable for 45 s and the node is isolated (a peer still reaches ha-api, or no peer answers), it destroys the protected VMs of its own node before ha-api evacuates them (ha-api acts after 90 s).
- It does nothing when every peer also lost ha-api (ha-api or controller down), when no peer is configured, or when the protected list is empty. Dry-run mode only logs.
- A flag file stops compute-worker's auto-recovery from restarting the destroyed VMs until the operator resets the host.
- ha-api: `GET /internal/protected-domains/{host}`, `GET /internal/hosts/{host}`, `POST /internal/watchdog/{host}`; recovery notes "no watchdog seen" for hosts without recent watchdog contact.

## Known limitations
- Not power fencing (Masakari uses Pacemaker/Corosync with STONITH/IPMI). A frozen kernel or a hung watchdog does not fence itself; the timing margin (kill at about 45 s, evacuate at 90 s) is not a hard guarantee.
- Needs at least two compute nodes. If a partition cuts a node from ha-api and every peer, the node kills its protected VMs while ha-api may be unable to evacuate them.
- The protected list is cached and can be a few seconds stale; unprotecting a VM inside that window can still get it killed once.
