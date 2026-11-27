# Instance HA (Masakari-like)

Automatically recovers protected VMs when a compute node dies. It covers the basic Masakari features (host monitor and host recovery), not 1:1.

## Architecture

- `ha-api` (controller, port 8020, one process with one monitor thread). Tables `ha_hosts`, `ha_protected`, `ha_recoveries`. No new worker.
- `cloudx-compute-worker` sends a heartbeat every 10 seconds to `POST /internal/heartbeat/{host}`, together with that node's `initiator_iqn`. A failed send is only logged.
- block-store has a new internal endpoint `POST /internal/fence`: it asks the block-store-worker that owns each volume to run `unexport_volume` (revoking the iSCSI ACL). The result comes back per volume.
- compute: `PUT /os-services/{host}` and `POST /servers/{id}/evacuate` now accept the internal token as well as admin.

## Flow

1. The monitor checks every 10 seconds. A host that is `up` with a heartbeat older than 60 seconds becomes `suspect`.
2. If it is still silent after 30 more seconds, the host is considered dead. A heartbeat that comes back before then cancels everything (no fencing, no evacuate).
3. Only VMs in `ha_protected` are handled. With no protected VMs, the host is just marked `forced_down`.
4. Pre-check: a VM whose root or data volume is on the dead host, or whose location is unknown, is recorded as `skipped` and not evacuated.
5. Required order: `forced_down=true`, revoke the iSCSI ACL of every volume of the VMs that passed, and only then `evacuate`. If fencing fails there is no evacuate; the host stays `suspect` and is retried, at most 3 times, then it stops until an operator steps in.
6. Each VM is verified `ACTIVE` within 300 seconds. The result is in `GET /recoveries` (`done`, `failed`, `skipped`).
7. The host becomes `fenced`. A heartbeat that returns changes nothing.

## Recovering a node (manual)

1. Make sure the node is healthy; old VMs on that node may still be running and must be cleaned up (`virsh destroy` and `undefine`, iSCSI logout).
2. `PUT /os-services/{host}` with `{"forced_down": false}`.
3. `cloudx ha reset HOST`.

## Watchdog (self-fencing)

With no BMC in the lab, an isolated node shuts down its own protected VMs. The `cloudx-ha-watchdog` unit (root, on every compute node, separate from compute-worker) keeps the list of protected VMs from ha-api. When ha-api is unreachable for 45 seconds, it asks its peers (`GET /reach`):

- a peer can still reach ha-api, or no peer answers: this node is isolated, and the protected VMs are `virsh destroy`ed;
- no peer can reach ha-api either: ha-api or the controller is down, no action;
- no peers configured, or an empty list: no action.

Before `destroy`, the watchdog writes `/var/lib/cloudx/ha-watchdog/fenced`; while that file exists, compute-worker does not restart VMs that were shut down. The watchdog deletes the file after the operator returns the host to `up` (`forced_down=false`, then `cloudx ha reset HOST`). Any HTTP response from ha-api (including 5xx) counts as reachable. `ha_watchdog_dry_run = true` only writes to the log.

Configuration in `/etc/cloudx/ha-watchdog/ha-watchdog.conf` (`[DEFAULT]`): `ha_watchdog_host` (node name), `ha_watchdog_peers` (peer URLs, comma-separated), `ha_watchdog_timeout`, `ha_watchdog_dry_run`, `ha_watchdog_port` (8021). A node that runs the watchdog must also have `ha_api_url` in the same conf, comma-separated when there are several ha-api instances (`ha_api_url = http://a:8020,http://b:8020`). This override beats the service catalog, so the fencing decision does not depend on a catalog lookup. Without the override, the watchdog saves the last ha-api URL that resolved to `/var/lib/cloudx/ha-watchdog/ha_api_url` and uses it when the catalog is unreachable; only a watchdog that starts cold while auth is unreachable and has never saved a URL counts that failed lookup as "ha-api silent". The catalog also keeps only the first URL of a comma-separated list, so failover between instances needs this override. Migration `cloudx-db-migrate ha` (`ha_0002`). ha-api puts "no watchdog seen" in the recovery note when the host sent no watchdog signal in the last 120 seconds.

For comparison: Masakari detects failures through Pacemaker/Corosync and uses STONITH (IPMI) as external fencing (docs.openstack.org/masakari-monitors, hostmonitor). This watchdog is not a 1:1 replacement: it depends on the node being healthy enough to shut down its own VMs.

## Using it

- CLI: `cloudx ha hosts | protect SERVER | unprotect SERVER | protected | recoveries | reset HOST`.
- Dashboard: High availability.
- A VM can only be protected when all its volumes are on other hosts and its location is known (422 otherwise).
- A Ceph RBD volume has no host in CloudX, so a server with a Ceph volume (root or data) is refused with `422 server volumes could not be located`. Today only servers whose volumes are LVM volumes on other compute nodes can be protected. Found on the lab on 2026-11-27.
- A server that is `MIGRATING` is never evacuated automatically: it is recorded as `skipped` (`live migration in progress; check the target before evacuating`) and an operator decides (`docs/live-migration.md`). The watchdog does not look at the migration state: it destroys the protected domains of an isolated node also while one of them is being migrated away. On the lab this left no running copy of the guest: the server stays `MIGRATING` (message `... it is unclear where the guest runs`) until an operator settles it with the steps in `docs/live-migration.md`.

## Configuration

- Controller: `ha_api_url` in `cloudx.conf`. Unit `cloudx-ha-api`, migration `cloudx-db-migrate ha`.
- Every compute node: `ha_api_url = http://<controller>:8020` in `cloudx.conf`.

## Lab results

Two CirrOS VMs (one with its root on the storage node, one on the compute node), both protected.

- Positive: `cloudx-compute-worker` and libvirtd on the compute node were stopped. About 100 seconds later the host was `fenced`, VM `ha1` moved to the storage node and was `ACTIVE`, and the recovery was `done`. The iSCSI sessions on the target were zero and the target was torn down. The old qemu stayed alive on the dead node with its iSCSI session already cut; it can no longer write.
- Negative: the storage node was shut down. The VM whose root was on the storage node was recorded as `skipped` (`volume on failed host`) and not evacuated. The other VM was evacuated.
- Blip: the worker was stopped for 20 seconds and then started. No action; the host went back to `up`.
- Found during testing: `PUT /os-services` and `evacuate` originally accepted only an admin JWT, so ha-api was rejected (401). Fixed with `AdminOrInternal`.

Run again on 2026-11-27 with the 1.0.15 packages (ha-api on the three controllers, `comp01` and `comp02`, one protected CirrOS server `ha-t2` with an LVM root volume on the other compute node). The watchdog unit is disabled after an install and its conf is empty, so it was configured (`ha_watchdog_host`, `ha_watchdog_peers` pointing at the other node, no `ha_api_url` override) and started on both nodes. The ha-api path was cut with `iptables -I OUTPUT -p tcp --dport 8020 -j DROP`; use `DROP`, because a refused connection counts as reachable.

- Both nodes cut off for 56 seconds: at 45 s each watchdog logged `hold` (no peer reached ha-api either) and again at 55 s. No domain was destroyed, no fence flag was written, both hosts stayed `up`, no recovery was started.
- Only the node of the protected server cut off: at 45 s the watchdog logged `kill`, destroyed the domain and wrote the fence flag. ha-api marked the host `suspect` at 82 s, `recovering` at 105 s and `fenced` at 116 s; the recovery started 55 s after the destroy, was `done`, and the server was `ACTIVE` on the other node. A server that was not protected, on the same node, was not touched.
- A protected server migrating away from the node that was cut off (migration held at 1 MiB/s with `virsh migrate-setspeed`, so it was still running): at 45 s the watchdog destroyed the domain on the source and the migration failed (`domain is not running`); the domain on the target was removed. ha-api marked the host `suspect` at 64 s, recorded the recovery as `skipped` (`live migration in progress; check the target before evacuating`) at about 91 s and `fenced` the host at 104 s. The server stayed `MIGRATING` on the source with no running guest anywhere. After the rule was removed, the host reset and the server settled with the internal finish call (`result` `error`), it was `ACTIVE`, and the compute worker's auto-recovery started the shut-off domain about a minute later.
- When the guest had already moved before the cut (the migration took a few seconds), nothing was lost: the watchdog of the old node still used its cached list, tried to destroy the domain that is no longer there (`rc=1`) and wrote the fence flag; ha-api did not evacuate anything because no protected server was left on that host.
- Recovery by hand (rule removed, `forced_down` false, `cloudx ha reset comp01`): the host was `up` again, the watchdog removed the fence flag, and compute-worker destroyed the stale domain on the old node (`host in API is comp02`).

## Two ha-api instances

A second instance may run on another node (the same `cloudx-ha-api` unit). All endpoints are active on both; only one instance runs the monitor loop, the holder of a `pg_try_advisory_lock` on the shared PostgreSQL. The lock is released automatically when the process or its connection dies, and the other instance takes it within about 10 seconds. A new leader gives every host a fresh `last_seen` (a 60-second grace period before it is judged).

Install on the second node:
1. `/etc/cloudx/ha-api/ha-api.conf` (`[DEFAULT]`) with `database_url` pointing at the controller's PostgreSQL, and `compute_api_url`, `block_store_api_url`, `auth_api_url` pointing at the controller.
2. On the controller: a `pg_hba.conf` line for that node's IP only (`/32`, `scram-sha-256`), and open 5432 only from that node.
3. On all compute nodes: `ha_api_url = http://controller:8020,http://node2:8020`. compute-worker sends its heartbeat to the first instance that answers; the watchdog treats ha-api as reachable if either one answers.

Limits: PostgreSQL, compute-api, block-store-api and auth stay on the controller. If the controller dies, the second instance is alive but cannot evacuate. The dashboard only knows one ha-api URL.

Lab results (two instances: the controller and a compute node): exactly one leader; the controller's ha-api was stopped and the compute node took the lock within 2 seconds; with the old leader down, compute-worker and libvirtd on the compute node were stopped and the second instance evacuated the protected VM to the storage node (recovery `done`); the old ha-api was started again, became a follower, and there was no double evacuation. With the controller's ha-api down, heartbeats and protection still worked through the second instance.

## Cleanup after evacuate

When the old node recovers, compute-worker deletes domains that the API says have moved host, then logs out the iSCSI sessions of those domains' disks. Orphan sessions with no domain are not cleaned up.

## Limits

See the release notes (`docs/releases/v0.8.5.md`).
