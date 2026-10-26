# Volume replication (Phase 3) — design

## Goal
Replace `enable/sync/failover/disable-replication`, which today only change a status column, with real data replication between LVM storage nodes. Success = the replica's data is identical to the source (sha256), and failover brings the volume back with that data from another node.

## Constraints
- LVM driver only. Other drivers refuse with 400 `replication not supported by driver`.
- No SSH and no new packages. Standard library only.
- Auth between workers: `internal_token` (HMAC), consistent with the existing service-to-service auth.
- Config through `/etc/cloudx/block-store-worker/*.conf`, not environment variables in systemd.

## Flow
1. **enable** (`POST /volumes/{ref}/enable-replication`, `target` = an LVM host):
   the target must be in `LVM_HOSTS`, must not be `volume.host`, and the volume must be `available`/`in-use`. The API sends an RPC to the target worker to create an LV `rep-<volume_id>` of the same size, then the status becomes `enabled`.
2. **sync** (`POST /volumes/{ref}/sync-replication`): status `replicating`. The source worker takes a temporary LVM snapshot and streams it to the receiver in the target worker over a one-shot TCP connection (header: volume, size, HMAC nonce; stream; sha256 at the end). The target compares the sha256, then `synced` + `last_replicated_at`. On failure => `error` + message; the temporary snapshot is always deleted.
3. **failover** (`POST /volumes/{ref}/failover`): the replica LV becomes the primary volume (`volume.host = target`, the LV is renamed to the volume's name), status `failed-over`. Refused with 409 when the volume is `in-use`, unless `force=true` (the source node is dead). The old LV is not deleted automatically.
4. **disable**: the replica is deleted on the target, status `disabled`.

## Components
- `block_store/app/routers/replication.py`: validation + RPC (follow the existing messaging RPC pattern).
- `block_store_worker/app/drivers/lvm.py`: `replica_create`, `replica_send`, `replica_promote`, `replica_delete`.
- `block_store_worker/app/replication.py` (new, about 40 lines): the TCP receiver (port from the conf, accepts only the source host's IP) and the sender.
- Migration: only if the error message column does not exist yet (check `replication_*` in the model).

## Testing
- Unit (mock `run`/socket): target validation, non-LVM driver rejected, wrong HMAC rejected, different sha256 => `error`, temporary snapshot deleted on failure, `in-use` failover without force => 409.
- Live (compute node → storage node): write a pattern, sync, replica md5 equal; change the source, sync, md5 equal; stop the source worker, `force` failover, data matches; clean disable. Remove every `zz-` LV/volume afterwards.

## Deliberately postponed (`ponytail:`)
Sync is always full (incremental via `thin_delta` later); manual sync without a schedule; one replica per volume; no automatic failback; `async`/`sync` are only labels; the stream is not encrypted (HMAC only authenticates; TLS if the storage network is not trusted).
