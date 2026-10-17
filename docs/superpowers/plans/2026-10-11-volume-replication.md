# Replikasi volume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** `enable/sync/failover/disable-replication` menyalin data LV nyata antar node storage LVM.

**Architecture:** API memvalidasi dan mengirim RPC cast ke antrean host (`host_queue("block-store", host)`, pola `clients.py:58`). Worker sumber membuat snapshot LVM dan mengalirkannya lewat TCP+HMAC ke receiver di worker target. Hasil dilaporkan ke conductor lewat `_rpc.cast("conductor", "update_volume", ...)`.

**Tech Stack:** FastAPI, RabbitMQ RPC (`shared.rpc`), LVM, stdlib `socket`/`hmac`/`hashlib`.

**Spec:** `docs/superpowers/specs/2026-10-11-volume-replication-design.md`

## Global Constraints
- Hanya driver LVM; driver lain: 400 `replication not supported by driver`.
- Tanpa SSH, tanpa dependensi baru. Auth: HMAC-SHA256 dengan `internal_token`.
- Config lewat `/etc/cloudx/block-store-worker/*.conf` (port receiver `replication_port`, default 9870), bukan env systemd.
- Subproses lewat `shared.proc.run`. `except Exception` wajib `# best-effort: <alasan>`.
- Jangan commit/push; jangan `git checkout/stash/restore/reset`.

## Review Focus
- Target = host sumber, atau bukan `LVM_HOSTS`: 400.
- HMAC salah / IP peer bukan host sumber: koneksi ditolak, tidak ada LV tertimpa.
- sha256 beda atau koneksi putus: status `error`, snapshot sementara tetap dihapus.
- Failover volume `in-use` tanpa `force`: 409; dengan `force`: lanjut.
- Sync saat status `disabled` atau saat sync lain berjalan (`replicating`): 400/409.

### Task 1: Driver LVM + transfer
**Files:** Modify `services/block_store_worker/app/drivers/lvm.py`; Create `services/block_store_worker/app/replication.py`; Test `tests/block_store/test_replication_transfer.py`

**Interfaces:**
- Produces (`lvm.py`): `replica_create(volume_id: str, size_gb: int) -> str` (LV `rep-<volume_id>`, balik path), `replica_delete(volume_id: str) -> None`, `replica_promote(volume_id: str, volume_name: str) -> str` (lvrename `rep-<id>` → `volume_name`, balik path).
- Produces (`replication.py`): `serve(port: int, token: str, allowed_peer: Callable[[str], bool]) -> None` (thread daemon, tiap koneksi: baca header JSON `{volume_id, size, nonce, mac}`, cek `mac == hmac(token, volume_id|size|nonce)`, tulis ke `/dev/<vg>/rep-<id>`, balas sha256 hex); `send(dev_path: str, volume_id: str, size: int, host: str, port: int, token: str) -> str` (stream, balik sha256 lokal; raise `ReplicationError` bila sha256 target beda).

- [ ] **Step 1: tes gagal.** `test_roundtrip_matches_sha256` (server+client di localhost, file biasa sebagai "dev"; assert data identik dan sha256 sama), `test_bad_hmac_rejected` (token beda: tidak ada tulisan, `ReplicationError`), `test_digest_mismatch_raises`, `test_peer_not_allowed_rejected`.
- [ ] **Step 2:** jalankan `python -m pytest tests/block_store/test_replication_transfer.py -q -W ignore` → FAIL.
- [ ] **Step 3:** implementasi sesuai signature di atas. Path tujuan dapat di-inject (parameter `open_dev`) supaya tes memakai file biasa. Chunk 4 MiB. `# ponytail: sync penuh, inkremental via thin_delta bila perlu`.
- [ ] **Step 4:** tes PASS.

### Task 2: Handler worker
**Files:** Modify `services/block_store_worker/app/main.py` (daftar handler di dekat `handle_migrate_volume`, mulai `replication.serve` di startup dengan token+port dari config); Test `tests/block_store/test_replication_handlers.py`

**Interfaces:**
- Consumes: Task 1.
- Produces handler RPC: `handle_replica_create(args)`, `handle_replica_sync(args)` (`{volume_id, target_host, target_ip}`: snapshot sementara → `replication.send` → `finally` hapus snapshot → cast `update_volume` `replication_status=synced|error`, `last_replicated_at`), `handle_replica_promote(args)`, `handle_replica_delete(args)`.

- [ ] **Step 1:** tes dengan mock `lvm`/`replication`: sukses → `synced`; `ReplicationError` → `error` dan `lvremove` snapshot tetap dipanggil.
- [ ] **Step 2:** FAIL. **Step 3:** implementasi. **Step 4:** PASS.

### Task 3: API
**Files:** Modify `services/block_store/app/routers/replication.py`, `services/block_store/app/schemas.py` (`ReplicationFailoverIn{force: bool=False}`); Update `tests/block_store/test_replication.py` (target "dr-pool-node-2" kini ditolak: pakai host dari `LVM_HOSTS`); Test `tests/block_store/test_replication_api.py`

**Interfaces:**
- Consumes: handler Task 2 lewat `clients` cast ke `host_queue("block-store", host)` (lihat `clients.py:58`).
- Produces: `enable` memvalidasi target ∈ `LVM_HOSTS`, ≠ `volume.host`, driver LVM, lalu cast `replica_create`; `sync` → status `replicating` + cast `replica_sync`, 409 bila sudah `replicating`; `failover` (409 bila `in-use` tanpa `force`) → cast `replica_promote`, set `volume.host=target`, `failed-over`; `disable` → cast `replica_delete`.

- [ ] **Step 1:** tes untuk semua baris Review Focus (400/409) dengan cast di-mock.
- [ ] **Step 2:** FAIL. **Step 3:** implementasi. **Step 4:** `python -m pytest tests/block_store tests/shared/test_migrations.py -q -W ignore` PASS, ruff bersih.

### Task 4: CLI + docs
**Files:** Modify `cli/cloudx_cli/commands/volume.py` (cek apakah `enable-replication` dst. sudah ada; tambahkan yang kurang, `failover --force`), `tests/cli/test_help.py`; Modify docs terkait replikasi (hapus "control-plane only").

- [ ] Verifikasi `--help` menampilkan perintah; `python -m pytest tests/cli -q -W ignore` PASS.

### Task 5: Deploy + tes live (lab)
Gunakan `cloudx-deploy`, lalu `cloudx-e2e`. Tambahkan `replication_port` dan firewall bila perlu. Skenario spec: pola → sync → md5 sama (compute→storage); ubah sumber → sync → sama; hentikan worker sumber → `failover --force` → md5 cocok; disable bersih. Bersihkan LV `zz-`.
