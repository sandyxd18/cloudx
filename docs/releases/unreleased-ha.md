# CloudX (version to be assigned) - Instance HA

New `ha-api` (port 8020): automatic recovery of protected servers when a compute node dies (Masakari-like host monitor and host recovery, not 1:1).

## Highlights

- Compute workers send a heartbeat every 10 s. A host silent for 60 s and still silent 30 s later is declared dead.
- Fixed order: mark `forced_down`, revoke the dead node's iSCSI ACLs on the storage nodes, then evacuate. If fencing fails nothing is evacuated; the host stays `suspect` and is retried up to 3 times.
- Only servers you protect are evacuated (`cloudx ha protect`, API `PUT /protected/{id}`, dashboard High availability). Each evacuation is checked to reach `ACTIVE` within 300 s and logged in `/recoveries`.
- Servers whose root or data volume lives on the failed host are skipped, never evacuated. Protecting such a server is refused (422).
- New `POST /internal/fence` on block-store. `PUT /os-services/{host}` and `POST /servers/{id}/evacuate` now also accept the internal token.

## Upgrading

1. Deploy, run `cloudx-db-migrate ha` on the controller.
2. Copy and enable `cloudx-ha-api`; set `ha_api_url` in `cloudx.conf` on the controller and on every compute node (pointing at the controller).
3. Redeploy `compute-worker` and `block-store` so heartbeat and fence are available.

## Limitations

- Host monitor only: no process or VM monitors.
- No power fencing (no BMC in the lab). A hung node may keep running its VMs; it is only blocked from writing through iSCSI, not powered off. Its old VMs must be cleaned up by hand before the host returns.
- Servers with a volume on the failed host are not recovered (their data lives only there).
- Node recovery is manual: `forced_down=false`, then `cloudx ha reset HOST`.
- No evacuation priority or ordering between servers.
- A host that never reported an iSCSI initiator name cannot be fenced and is never auto-evacuated.
- Heartbeats live in the database, so a restart of ha-api gives every host a fresh 60 s before judging it.
- Fencing relies on revoking the LIO ACL cutting the open session; verified in the lab with the lab's targetcli version.
