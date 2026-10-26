# Volume replication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** `enable/sync/failover/disable-replication` copy real LV data between LVM storage nodes.

**Architecture:** The API validates and sends an RPC cast to the host queue (`host_queue("block-store", host)`, the pattern in `clients.py:58`). The source worker takes an LVM snapshot and streams it over TCP+HMAC to the receiver in the target worker. The result is reported to the conductor through `_rpc.cast("conductor", "update_volume", ...)`.

**Tech Stack:** FastAPI, RabbitMQ RPC (`shared.rpc`), LVM, stdlib `socket`/`hmac`/`hashlib`.

**Spec:** `docs/superpowers/specs/2026-10-11-volume-replication-design.md`

## Global Constraints
- LVM driver only; other drivers: 400 `replication not supported by driver`.
- No SSH, no new dependencies. Auth: HMAC-SHA256 with `internal_token`.
- Config through `/etc/cloudx/block-store-worker/*.conf` (receiver port `replication_port`, default 9870), not systemd environment variables.
- Subprocesses go through `shared.proc.run`. A broad `except Exception` must carry `# best-effort: <reason>`.
- Do not commit/push; do not `git checkout/stash/restore/reset`.

## Review Focus
- Target = the source host, or not in `LVM_HOSTS`: 400.
- Wrong HMAC / peer IP is not the source host: the connection is refused, no LV is overwritten.
- Different sha256 or a dropped connection: status `error`, the temporary snapshot is still deleted.
- Failover of an `in-use` volume without `force`: 409; with `force`: proceeds.
- Sync while the status is `disabled` or while another sync runs (`replicating`): 400/409.

### Task 1: LVM driver + transfer
**Files:** Modify `services/block_store_worker/app/drivers/lvm.py`; Create `services/block_store_worker/app/replication.py`; Test `tests/block_store/test_replication_transfer.py`

**Interfaces:**
- Produces (`lvm.py`): `replica_create(volume_id: str, size_gb: int) -> str` (LV `rep-<volume_id>`, returns the path), `replica_delete(volume_id: str) -> None`, `replica_promote(volume_id: str, volume_name: str) -> str` (lvrename `rep-<id>` → `volume_name`, returns the path).
- Produces (`replication.py`): `serve(port: int, token: str, allowed_peer: Callable[[str], bool]) -> None` (daemon thread; for each connection: read the JSON header `{volume_id, size, nonce, mac}`, check `mac == hmac(token, volume_id|size|nonce)`, write to `/dev/<vg>/rep-<id>`, reply with the sha256 hex); `send(dev_path: str, volume_id: str, size: int, host: str, port: int, token: str) -> str` (streams, returns the local sha256; raises `ReplicationError` if the target's sha256 differs).

- [ ] **Step 1: failing tests.** `test_roundtrip_matches_sha256` (server+client on localhost, a plain file as the "dev"; assert identical data and equal sha256), `test_bad_hmac_rejected` (different token: nothing written, `ReplicationError`), `test_digest_mismatch_raises`, `test_peer_not_allowed_rejected`.
- [ ] **Step 2:** run `python -m pytest tests/block_store/test_replication_transfer.py -q -W ignore` → FAIL.
- [ ] **Step 3:** implement to the signatures above. The destination path can be injected (parameter `open_dev`) so tests use a plain file. 4 MiB chunks. `# ponytail: full sync, incremental via thin_delta if needed`.
- [ ] **Step 4:** tests PASS.

### Task 2: Worker handlers
**Files:** Modify `services/block_store_worker/app/main.py` (handler list near `handle_migrate_volume`, start `replication.serve` at startup with the token+port from the config); Test `tests/block_store/test_replication_handlers.py`

**Interfaces:**
- Consumes: Task 1.
- Produces RPC handlers: `handle_replica_create(args)`, `handle_replica_sync(args)` (`{volume_id, target_host, target_ip}`: temporary snapshot → `replication.send` → `finally` delete the snapshot → cast `update_volume` `replication_status=synced|error`, `last_replicated_at`), `handle_replica_promote(args)`, `handle_replica_delete(args)`.

- [ ] **Step 1:** tests with `lvm`/`replication` mocked: success → `synced`; `ReplicationError` → `error` and the snapshot `lvremove` is still called.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS.

### Task 3: API
**Files:** Modify `services/block_store/app/routers/replication.py`, `services/block_store/app/schemas.py` (`ReplicationFailoverIn{force: bool=False}`); Update `tests/block_store/test_replication.py` (target "dr-pool-node-2" is now rejected: use a host from `LVM_HOSTS`); Test `tests/block_store/test_replication_api.py`

**Interfaces:**
- Consumes: the Task 2 handlers through a `clients` cast to `host_queue("block-store", host)` (see `clients.py:58`).
- Produces: `enable` validates target ∈ `LVM_HOSTS`, ≠ `volume.host`, LVM driver, then casts `replica_create`; `sync` → status `replicating` + cast `replica_sync`, 409 if already `replicating`; `failover` (409 if `in-use` without `force`) → cast `replica_promote`, set `volume.host=target`, `failed-over`; `disable` → cast `replica_delete`.

- [ ] **Step 1:** tests for every Review Focus line (400/409) with the cast mocked.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** `python -m pytest tests/block_store tests/shared/test_migrations.py -q -W ignore` PASS, ruff clean.

### Task 4: CLI + docs
**Files:** Modify `cli/cloudx_cli/commands/volume.py` (check whether `enable-replication` etc. already exist; add what is missing, `failover --force`), `tests/cli/test_help.py`; Modify the replication docs (remove "control-plane only").

- [ ] Verify `--help` shows the commands; `python -m pytest tests/cli -q -W ignore` PASS.

### Task 5: Deploy + live test (lab)
Use `cloudx-deploy`, then `cloudx-e2e`. Add `replication_port` and the firewall rule if needed. Spec scenario: pattern → sync → same md5 (compute→storage); change the source → sync → same; stop the source worker → `failover --force` → md5 matches; clean disable. Remove the `zz-` LVs.
