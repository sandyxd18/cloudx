"""
cloudx Conductor — nova-conductor equivalent
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

from __future__ import annotations

import logging
import os
import sys
import time

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

# Append parent path so shared/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from shared.rpc import RPCClient, RPCConsumer  # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv("DATABASE_URL",
    "postgresql+psycopg://mini:mini@postgres:5432/mini_openstack")
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://cloudx:cloudx@rabbitmq:5672/")
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

    with SessionLocal() as db:
        result = db.execute(text(sql), params)
        db.commit()

    log.info("Updated server %s: %s (rows=%d)", server_id,
             list(safe_fields.keys()), result.rowcount)

    # Publish event
    _publish_server_event("server.update", server_id, safe_fields)

    return {"ok": True, "updated": list(safe_fields.keys())}


def handle_delete_server_record(args: dict) -> dict:
    """Hard-delete a server record from the database.

    Expected args::

        {"server_id": "uuid"}
    """
    server_id = args.get("server_id")
    if not server_id:
        return {"error": "server_id required"}

    with SessionLocal() as db:
        result = db.execute(
            text("DELETE FROM compute_servers WHERE id = :sid"),
            {"sid": server_id},
        )
        db.commit()

    log.info("Deleted server record %s (rows=%d)", server_id, result.rowcount)
    _publish_server_event("server.delete", server_id)

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

    with SessionLocal() as db:
        db.execute(
            text("UPDATE compute_servers SET status = 'ERROR', error_message = :msg WHERE id = :sid"),
            {"sid": server_id, "msg": error_msg},
        )
        db.commit()

    log.info("Marked server %s as ERROR: %s", server_id, error_msg)
    _publish_server_event("server.update", server_id,
                          {"status": "ERROR", "error_message": error_msg})

    return {"ok": True}


# ─── Block Storage Handlers ──────────────────────────────────────────────────

# Allowed fields for volume updates from worker
ALLOWED_VOLUME_FIELDS = {
    "status", "path", "size_gb", "error_message",
    "attached_to_server_id", "target_device",
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

    safe = {k: v for k, v in fields.items() if k in ALLOWED_VOLUME_FIELDS}
    if not safe:
        return {"ok": True, "updated": []}

    set_parts = []
    params = {"_vid": volume_id}
    for key, value in safe.items():
        set_parts.append(f"{key} = :_{key}")
        params[f"_{key}"] = value

    sql = f"UPDATE block_store_volumes SET {', '.join(set_parts)} WHERE id = :_vid"
    with SessionLocal() as db:
        result = db.execute(text(sql), params)
        db.commit()

    log.info("Updated volume %s: %s (rows=%d)", volume_id, list(safe.keys()), result.rowcount)
    _publish_volume_event("volume.update", volume_id, safe)
    return {"ok": True, "updated": list(safe.keys())}


def handle_delete_volume_record(args: dict) -> dict:
    """Hard-delete a volume record from the database.

    Expected args::

        {"volume_id": "uuid"}
    """
    volume_id = args.get("volume_id")
    if not volume_id:
        return {"error": "volume_id required"}

    with SessionLocal() as db:
        result = db.execute(
            text("DELETE FROM block_store_volumes WHERE id = :vid"),
            {"vid": volume_id},
        )
        db.commit()

    log.info("Deleted volume record %s (rows=%d)", volume_id, result.rowcount)
    _publish_volume_event("volume.delete", volume_id)
    return {"ok": True, "deleted": result.rowcount}


def handle_mark_volume_error(args: dict) -> dict:
    """Set status=error and error_message on a volume."""
    volume_id = args.get("volume_id")
    error_msg = args.get("error_message", "unknown error")
    if not volume_id:
        return {"error": "volume_id required"}

    with SessionLocal() as db:
        db.execute(
            text("UPDATE block_store_volumes SET status = 'error', error_message = :msg WHERE id = :vid"),
            {"vid": volume_id, "msg": error_msg},
        )
        db.commit()

    log.info("Marked volume %s as error: %s", volume_id, error_msg)
    _publish_volume_event("volume.update", volume_id, {"status": "error", "error_message": error_msg})
    return {"ok": True}


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
    "update_volume":         handle_update_volume,
    "delete_volume_record":  handle_delete_volume_record,
    "mark_volume_error":     handle_mark_volume_error,
    "update_snapshot":       handle_update_snapshot,
    "delete_snapshot_record": handle_delete_snapshot_record,
    "mark_snapshot_error":   handle_mark_snapshot_error,
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
