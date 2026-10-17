from __future__ import annotations
from shared.config import load_service_config
load_service_config("conductor")
"""
cloudx Conductor — Compute Conductor Service
==============================================

AMQP consumer that acts as a DB proxy for compute and block-store workers.
Workers never access the database directly; they call the conductor
via RPC to update resource status, fields, and to delete records.

The conductor also publishes **live events** to the ``cloudx.events``
fanout exchange, which the dashboard subscribes to for real-time updates.

Queue: ``conductor``
Exchange: ``cloudx`` (topic)

Handlers
--------
Compute:
- ``update_server``        — update status and/or fields on a server record
- ``delete_server_record`` — hard-delete a server record from DB
- ``mark_server_error``    — set status=ERROR + error_message
- ``get_host_servers``     — list non-deleted servers assigned to a host
  (used by compute-worker's power-state reconciliation loop)

Block Storage:
- ``update_volume``        — update status and/or fields on a volume
- ``delete_volume_record`` — hard-delete a volume record from DB
- ``mark_volume_error``    — set status=error + error_message on a volume
- ``update_snapshot``      — update status/path on a snapshot
- ``delete_snapshot_record`` — hard-delete a snapshot record
- ``mark_snapshot_error``  — set status=error on a snapshot
"""


import logging
import os
import sys
import time
from datetime import datetime

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Append parent path so shared/ is importable
from shared.rpc import RPCClient, RPCConsumer  # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv("DATABASE_URL",
    "postgresql+psycopg://cloudx:cloudx@127.0.0.1:5432/cloudx")
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://cloudx:cloudx@127.0.0.1:5672/")
LOG_LEVEL    = os.getenv("LOG_LEVEL", "INFO")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("conductor")

# ── Database ──────────────────────────────────────────────────────────────────

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5)
SessionLocal = sessionmaker(bind=engine)

# ── RPC Client (for publishing events) ────────────────────────────────────────

rpc_client = RPCClient(RABBITMQ_URL)

# ── Helpers ───────────────────────────────────────────────────────────────────

# Fields that workers are allowed to update on compute_servers
ALLOWED_SERVER_FIELDS = {
    "status", "error_message",
    "host", "instance_dir", "disk_path", "config_drive_path",
    "libvirt_domain_name", "tap_name", "mac_address", "ip_address",
}


def _publish_server_event(event_type: str, server_id: str,
                          extra: dict | None = None) -> None:
    """Publish a live event to the dashboard."""
    payload = {"server_id": server_id, **(extra or {})}
    try:
        rpc_client.publish_event(event_type, payload)
    except Exception as exc:
        log.warning("Failed to publish event %s: %s", event_type, exc)


def _publish_volume_event(event_type: str, volume_id: str,
                          extra: dict | None = None) -> None:
    payload = {"volume_id": volume_id, **(extra or {})}
    try:
        rpc_client.publish_event(event_type, payload)
    except Exception as exc:
        log.warning("Failed to publish event %s: %s", event_type, exc)


def _row(table: str, rid: str, cols: str = "project_id, status") -> dict:
    """Best-effort lookup of a row's columns; never raises."""
    try:
        with SessionLocal() as db:
            r = db.execute(text(f"SELECT {cols} FROM {table} WHERE id = :i"), {"i": rid}).mappings().first()
        return dict(r) if r else {}
    except Exception as exc:
        log.debug("lookup %s %s failed: %s", table, rid, exc)
        return {}


def _pid(prev: dict) -> dict:
    return {"project_id": prev["project_id"]} if prev.get("project_id") else {}


def _lifecycle_server(server_id: str, prev: dict, new_status: str | None, extra: dict | None = None) -> None:
    """Emit compute.instance.* lifecycle events on status transitions."""
    old = prev.get("status")
    ev = None
    if new_status == "ACTIVE" and old == "BUILD":
        ev = "compute.instance.create.end"
    elif new_status == "DELETED" and old != "DELETED":
        ev = "compute.instance.delete.end"
    elif new_status in ("RESIZE", "VERIFY_RESIZE") and old != new_status:
        ev = "compute.instance.resize"
    elif new_status == "ERROR" and old != "ERROR":
        ev = "compute.instance.crashed"
    if ev:
        _publish_server_event(ev, server_id, {**_pid(prev), "status": new_status, **(extra or {})})


# ═══════════════════════════════════════════════════════════════════════════════
# Handlers
# ═══════════════════════════════════════════════════════════════════════════════

def handle_update_server(args: dict) -> dict:
    """Update one or more fields on a server record.

    Expected args::

        {
            "server_id": "uuid",
            "fields": {
                "status": "ACTIVE",
                "host": "node-1",
                ...
            }
        }
    """
    server_id = args.get("server_id")
    fields = args.get("fields", {})

    if not server_id:
        return {"error": "server_id required"}

    # Filter to allowed fields only
    safe_fields = {k: v for k, v in fields.items() if k in ALLOWED_SERVER_FIELDS}
    if not safe_fields:
        return {"ok": True, "updated": []}

    # Build SET clause dynamically
    set_parts = []
    params = {"_sid": server_id}
    for key, value in safe_fields.items():
        set_parts.append(f"{key} = :_{key}")
        params[f"_{key}"] = value

    sql = f"UPDATE compute_servers SET {', '.join(set_parts)} WHERE id = :_sid"
    prev = _row("compute_servers", server_id)

    with SessionLocal() as db:
        result = db.execute(text(sql), params)
        db.commit()

    log.info("Updated server %s: %s (rows=%d)", server_id,
             list(safe_fields.keys()), result.rowcount)

    # Publish event
    _publish_server_event("server.update", server_id, {**_pid(prev), **safe_fields})
    _lifecycle_server(server_id, prev, safe_fields.get("status"),
                      {"error_message": safe_fields["error_message"]} if safe_fields.get("error_message") else None)

    return {"ok": True, "updated": list(safe_fields.keys())}


def handle_delete_server_record(args: dict) -> dict:
    """Hard-delete a server record from the database.

    Expected args::

        {"server_id": "uuid"}
    """
    server_id = args.get("server_id")
    if not server_id:
        return {"error": "server_id required"}

    prev = _row("compute_servers", server_id)
    with SessionLocal() as db:
        result = db.execute(
            text("DELETE FROM compute_servers WHERE id = :sid"),
            {"sid": server_id},
        )
        db.commit()

    log.info("Deleted server record %s (rows=%d)", server_id, result.rowcount)
    _publish_server_event("server.delete", server_id, _pid(prev))
    if prev and prev.get("status") != "DELETED":  # soft-delete path already emitted delete.end
        _publish_server_event("compute.instance.delete.end", server_id, _pid(prev))

    return {"ok": True, "deleted": result.rowcount}


def handle_get_host_servers(args: dict) -> dict:
    """List non-deleted servers assigned to a compute host.

    Used by compute-worker's power-state reconciliation loop to know which
    servers it is responsible for, without granting the worker direct DB
    access (see database_migration_standard skill).

    Expected args::

        {"host": "single-node"}
    """
    host = args.get("host")
    if not host:
        return {"error": "host required"}

    with SessionLocal() as db:
        rows = db.execute(
            text(
                "SELECT id, name, status, libvirt_domain_name, tap_name "
                "FROM compute_servers WHERE host = :host AND status != 'DELETED'"
            ),
            {"host": host},
        ).mappings().all()

    return {"servers": [dict(row) for row in rows]}


def handle_mark_server_error(args: dict) -> dict:
    """Convenience handler — set status=ERROR + error_message.

    Expected args::

        {"server_id": "uuid", "error_message": "something broke"}
    """
    server_id = args.get("server_id")
    error_msg = args.get("error_message", "unknown error")

    if not server_id:
        return {"error": "server_id required"}

    prev = _row("compute_servers", server_id)
    with SessionLocal() as db:
        db.execute(
            text("UPDATE compute_servers SET status = 'ERROR', error_message = :msg WHERE id = :sid"),
            {"sid": server_id, "msg": error_msg},
        )
        db.commit()

    log.info("Marked server %s as ERROR: %s", server_id, error_msg)
    _publish_server_event("server.update", server_id,
                          {**_pid(prev), "status": "ERROR", "error_message": error_msg})
    _lifecycle_server(server_id, prev, "ERROR", {"error_message": error_msg})

    return {"ok": True}


# ─── Block Storage Handlers ──────────────────────────────────────────────────

# Allowed fields for volume updates from worker
ALLOWED_VOLUME_FIELDS = {
    "status", "path", "size_gb", "error_message",
    "attached_to_server_id", "target_device",
    "replication_status", "replication_target", "replication_mode", "last_replicated_at", "host",
}

ALLOWED_SNAPSHOT_FIELDS = {"status", "path", "error_message"}


def handle_update_volume(args: dict) -> dict:
    """Update one or more fields on a block_store_volumes record.

    Expected args::

        {
            "volume_id": "uuid",
            "fields": {"status": "available", "path": "/dev/vg/vol-xxx"}
        }
    """
    volume_id = args.get("volume_id")
    fields    = args.get("fields", {})
    if not volume_id:
        return {"error": "volume_id required"}

    # Validate last_replicated_at timestamp format if present
    if "last_replicated_at" in fields:
        val = fields["last_replicated_at"]
        if isinstance(val, str):
            try:
                dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
                fields["last_replicated_at"] = dt.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                # Garbage date format -> ignore/drop field
                fields = {k: v for k, v in fields.items() if k != "last_replicated_at"}

    safe = {k: v for k, v in fields.items() if k in ALLOWED_VOLUME_FIELDS}
    if not safe:
        return {"ok": True, "updated": []}

    set_parts = []
    params = {"_vid": volume_id}
    for key, value in safe.items():
        set_parts.append(f"{key} = :_{key}")
        params[f"_{key}"] = value

    sql = f"UPDATE block_store_volumes SET {', '.join(set_parts)} WHERE id = :_vid"
    prev = _row("block_store_volumes", volume_id, "project_id, status, attached_to_server_id")
    with SessionLocal() as db:
        result = db.execute(text(sql), params)
        db.commit()

    log.info("Updated volume %s: %s (rows=%d)", volume_id, list(safe.keys()), result.rowcount)
    _publish_volume_event("volume.update", volume_id, {**_pid(prev), **safe})
    if safe.get("status") == "available" and prev.get("status") == "creating":
        _publish_volume_event("volume.create.end", volume_id, {**_pid(prev), "status": "available"})
    if "attached_to_server_id" in safe:
        new_srv, old_srv = safe["attached_to_server_id"], prev.get("attached_to_server_id")
        if new_srv and new_srv != old_srv:
            _publish_volume_event("volume.attach", volume_id, {**_pid(prev), "server_id": new_srv})
        elif not new_srv and old_srv:
            _publish_volume_event("volume.detach", volume_id, {**_pid(prev), "server_id": old_srv})
    return {"ok": True, "updated": list(safe.keys())}


def handle_delete_volume_record(args: dict) -> dict:
    """Hard-delete a volume record from the database.

    Expected args::

        {"volume_id": "uuid"}
    """
    volume_id = args.get("volume_id")
    if not volume_id:
        return {"error": "volume_id required"}

    prev = _row("block_store_volumes", volume_id)
    with SessionLocal() as db:
        result = db.execute(
            text("DELETE FROM block_store_volumes WHERE id = :vid"),
            {"vid": volume_id},
        )
        db.commit()

    log.info("Deleted volume record %s (rows=%d)", volume_id, result.rowcount)
    _publish_volume_event("volume.delete", volume_id, _pid(prev))
    _publish_volume_event("volume.delete.end", volume_id, _pid(prev))
    return {"ok": True, "deleted": result.rowcount}


def handle_mark_volume_error(args: dict) -> dict:
    """Set status=error and error_message on a volume."""
    volume_id = args.get("volume_id")
    error_msg = args.get("error_message", "unknown error")
    if not volume_id:
        return {"error": "volume_id required"}

    prev = _row("block_store_volumes", volume_id)
    with SessionLocal() as db:
        db.execute(
            text("UPDATE block_store_volumes SET status = 'error', error_message = :msg WHERE id = :vid"),
            {"vid": volume_id, "msg": error_msg},
        )
        db.commit()

    log.info("Marked volume %s as error: %s", volume_id, error_msg)
    _publish_volume_event("volume.update", volume_id, {**_pid(prev), "status": "error", "error_message": error_msg})
    return {"ok": True}


def handle_list_all_volumes(_args: dict) -> dict:
    """Return all volume records (used by block-store-worker reconciler)."""
    with SessionLocal() as db:
        rows = db.execute(
            text(
                "SELECT id, name, status, backend, host, path, size_gb, "
                "attached_to_server_id, target_device, project_id "
                "FROM block_store_volumes"
            ),
        ).mappings().all()
    return {"volumes": [dict(row) for row in rows]}


def handle_list_all_snapshots(_args: dict) -> dict:
    """Return all snapshot records (used by block-store-worker reconciler)."""
    with SessionLocal() as db:
        rows = db.execute(
            text(
                "SELECT s.id, s.name, s.status, s.backend, s.path, s.size_gb, s.volume_id, v.host "
                "FROM block_store_snapshots s LEFT JOIN block_store_volumes v ON v.id = s.volume_id"
            ),
        ).mappings().all()
    return {"snapshots": [dict(row) for row in rows]}


def handle_update_snapshot(args: dict) -> dict:
    """Update snapshot status/path.

    Expected args::

        {
            "snapshot_id": "uuid",
            "fields": {"status": "available", "path": "/dev/vg/snap-xxx"}
        }
    """
    snapshot_id = args.get("snapshot_id")
    fields      = args.get("fields", {})
    if not snapshot_id:
        return {"error": "snapshot_id required"}

    safe = {k: v for k, v in fields.items() if k in ALLOWED_SNAPSHOT_FIELDS}
    if not safe:
        return {"ok": True, "updated": []}

    set_parts = []
    params = {"_sid": snapshot_id}
    for key, value in safe.items():
        set_parts.append(f"{key} = :_{key}")
        params[f"_{key}"] = value

    sql = f"UPDATE block_store_snapshots SET {', '.join(set_parts)} WHERE id = :_sid"
    with SessionLocal() as db:
        result = db.execute(text(sql), params)
        db.commit()

    log.info("Updated snapshot %s: %s", snapshot_id, list(safe.keys()))
    return {"ok": True, "updated": list(safe.keys())}


def handle_delete_snapshot_record(args: dict) -> dict:
    """Hard-delete a snapshot record."""
    snapshot_id = args.get("snapshot_id")
    if not snapshot_id:
        return {"error": "snapshot_id required"}

    with SessionLocal() as db:
        result = db.execute(
            text("DELETE FROM block_store_snapshots WHERE id = :sid"),
            {"sid": snapshot_id},
        )
        db.commit()

    log.info("Deleted snapshot record %s", snapshot_id)
    return {"ok": True, "deleted": result.rowcount}


def handle_mark_snapshot_error(args: dict) -> dict:
    """Set status=error on a snapshot."""
    snapshot_id = args.get("snapshot_id")
    error_msg   = args.get("error_message", "unknown error")
    if not snapshot_id:
        return {"error": "snapshot_id required"}

    with SessionLocal() as db:
        db.execute(
            text("UPDATE block_store_snapshots SET status = 'error' WHERE id = :sid"),
            {"sid": snapshot_id},
        )
        db.commit()

    log.info("Marked snapshot %s as error: %s", snapshot_id, error_msg)
    return {"ok": True}


def handle_create_snapshot_record(args: dict) -> dict:
    """Insert a snapshot row (worker-created, e.g. per-volume snapshots of a CG snapshot)."""
    with SessionLocal() as db:
        db.execute(
            text("INSERT INTO block_store_snapshots (id, name, volume_id, size_gb, status, path, backend, created_at) "
                 "VALUES (:id, :name, :vol, :size, :status, :path, :backend, now()) ON CONFLICT (id) DO NOTHING"),
            {"id": args["snapshot_id"], "name": args["name"], "vol": args["volume_id"], "size": args.get("size_gb", 1),
             "status": args.get("status", "available"), "path": args.get("path", ""), "backend": args.get("backend", "lvm")},
        )
        db.commit()
    return {"ok": True}


def handle_update_cgsnapshot(args: dict) -> dict:
    """Set the status of a consistency-group snapshot."""
    with SessionLocal() as db:
        db.execute(text("UPDATE block_store_cgsnapshots SET status = :s WHERE id = :id"),
                   {"s": args["status"], "id": args["cgsnapshot_id"]})
        db.commit()
    return {"ok": True}


# ─── Volume Backup Handlers ──────────────────────────────────────────────────

ALLOWED_BACKUP_FIELDS = {"status", "object_key", "error_message"}


def handle_update_backup(args: dict) -> dict:
    """Update fields on a block_store_backups record."""
    backup_id = args.get("backup_id")
    fields    = args.get("fields", {})
    if not backup_id:
        return {"error": "backup_id required"}

    safe = {k: v for k, v in fields.items() if k in ALLOWED_BACKUP_FIELDS}
    if not safe:
        return {"ok": True, "updated": []}

    set_parts = []
    params = {"_bid": backup_id}
    for key, value in safe.items():
        set_parts.append(f"{key} = :_{key}")
        params[f"_{key}"] = value

    sql = f"UPDATE block_store_backups SET {', '.join(set_parts)} WHERE id = :_bid"
    with SessionLocal() as db:
        result = db.execute(text(sql), params)
        db.commit()

    log.info("Updated backup %s: %s (rows=%d)", backup_id, list(safe.keys()), result.rowcount)
    _publish_volume_event("backup.update", backup_id, safe)
    return {"ok": True, "updated": list(safe.keys())}


def handle_delete_backup_record(args: dict) -> dict:
    """Hard-delete a backup record."""
    backup_id = args.get("backup_id")
    if not backup_id:
        return {"error": "backup_id required"}
    with SessionLocal() as db:
        result = db.execute(
            text("DELETE FROM block_store_backups WHERE id = :bid"),
            {"bid": backup_id},
        )
        db.commit()
    log.info("Deleted backup record %s (rows=%d)", backup_id, result.rowcount)
    return {"ok": True, "deleted": result.rowcount}


def handle_mark_backup_error(args: dict) -> dict:
    """Set status=error on a backup."""
    backup_id = args.get("backup_id")
    error_msg = args.get("error_message", "unknown error")
    if not backup_id:
        return {"error": "backup_id required"}
    with SessionLocal() as db:
        db.execute(
            text("UPDATE block_store_backups SET status = 'error', error_message = :msg WHERE id = :bid"),
            {"bid": backup_id, "msg": error_msg},
        )
        db.commit()
    log.info("Marked backup %s as error: %s", backup_id, error_msg)
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════════════
# Handler dispatch table
# ═══════════════════════════════════════════════════════════════════════════════

HANDLERS = {
    # Compute
    "update_server":         handle_update_server,
    "delete_server_record":  handle_delete_server_record,
    "mark_server_error":     handle_mark_server_error,
    "get_host_servers":      handle_get_host_servers,
    # Block storage
    "list_all_volumes":      handle_list_all_volumes,
    "list_all_snapshots":    handle_list_all_snapshots,
    "update_volume":         handle_update_volume,
    "delete_volume_record":  handle_delete_volume_record,
    "mark_volume_error":     handle_mark_volume_error,
    "update_snapshot":       handle_update_snapshot,
    "delete_snapshot_record": handle_delete_snapshot_record,
    "mark_snapshot_error":   handle_mark_snapshot_error,
    "create_snapshot_record": handle_create_snapshot_record,
    "update_cgsnapshot":     handle_update_cgsnapshot,
    # Volume backups
    "update_backup":         handle_update_backup,
    "delete_backup_record":  handle_delete_backup_record,
    "mark_backup_error":     handle_mark_backup_error,
}


# ═══════════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    log.info("=" * 60)
    log.info("cloudx Conductor starting")
    log.info("  DB:       %s", DATABASE_URL.split("@")[-1])
    log.info("  RabbitMQ: %s", RABBITMQ_URL.split("@")[-1])
    log.info("  Handlers: %s", ", ".join(HANDLERS.keys()))
    log.info("=" * 60)

    # Wait for RabbitMQ to be ready
    for attempt in range(30):
        try:
            rpc_client._ensure_connection()
            log.info("RabbitMQ connection OK")
            break
        except Exception as exc:
            log.warning("Waiting for RabbitMQ (%d/30): %s", attempt + 1, exc)
            time.sleep(2)
    else:
        log.error("Could not connect to RabbitMQ after 30 attempts")
        sys.exit(1)

    consumer = RPCConsumer(
        queue_name="conductor",
        handlers=HANDLERS,
        rabbitmq_url=RABBITMQ_URL,
    )
    consumer.run()


if __name__ == "__main__":
    main()
