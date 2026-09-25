"""
cloudx Storage Reconciler (block-store-worker add-on)
=====================================================

Runs periodically to compare the DB (via conductor) against the physical
storage layer (LVM / File / Ceph) and corrects drift.

Drift scenarios this fixes:
  • LV / file / rbd image was deleted outside of our handlers (host reboot,
    manual cleanup, storage failure) → mark volume in DB as error.
  • Volume stuck in "deleting" but backend already gone → hard-delete DB record.
  • DB says error, but backend object re-appears (LV restored) → set available.

Scheduling: background thread (daemon) started in block-store-worker main.py,
sleeping RECONCILE_INTERVAL_SECONDS between passes. Default = 3600 (1 hour).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from typing import Any

sys = __import__("sys")
os_mod = os  # shadow to keep linter happy

# These imports resolve when the module is imported from the worker package.
from app.drivers import lvm, ceph  # noqa: E402

log = logging.getLogger("block-store-reconciler")

RECONCILE_INTERVAL = int(os.getenv("RECONCILE_INTERVAL_SECONDS", "60"))

# ── Statuses we are allowed to correct (same pattern as compute_worker) ───────
_RECONCILE_MANAGED_VOLUME_STATUSES = {
    "available",
    "in-use",
    "error",
    "deleting",
    "creating",
}


def _lv_name(volume_id: str) -> str:
    return f"vol-{volume_id.replace('-', '')[:24]}"


def _snap_lv_name(snapshot_id: str) -> str:
    return f"snap-{snapshot_id.replace('-', '')[:22]}"


def _backend_exists_for_volume(volume: dict) -> bool:
    """Return True if the physical storage object for *volume* is present."""
    backend = volume.get("backend", "lvm").lower()
    name = _lv_name(volume["id"])

    if backend == "lvm":
        return lvm.exists(name)
    if backend == "ceph":
        return ceph.exists(name)
    # file-based volumes (future / unsupported today)
    path = volume.get("path", "")
    if path:
        return os.path.exists(path)
    return False


def _backend_exists_for_snapshot(snap: dict) -> bool:
    """Return True if the physical snapshot object for *snap* is present."""
    backend = snap.get("backend", "lvm").lower()
    name = _snap_lv_name(snap["id"])

    if backend == "lvm":
        return lvm.exists(name)
    if backend == "ceph":
        return ceph.exists(name)
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# Reconciliation passes
# ═══════════════════════════════════════════════════════════════════════════════

def reconcile_volumes(rpc_client) -> None:
    """One pass: compare all DB volume records against physical storage."""
    try:
        result = rpc_client.call(
            "conductor", "list_all_volumes", {}, timeout=15
        )
    except Exception as exc:
        log.warning("[reconcile] could not fetch volume list: %s", exc)
        return

    volumes = result.get("volumes", [])
    if not volumes:
        return

    for vol in volumes:
        db_status = vol.get("status", "").lower()
        if db_status not in _RECONCILE_MANAGED_VOLUME_STATUSES:
            continue  # skip transient / handler-owned statuses

        physical_exists = _backend_exists_for_volume(vol)

        # 1. DB says available/in-use/creating/error but physical missing
        if not physical_exists and db_status in ("available", "in-use", "creating", "error"):
            log.warning(
                "[reconcile] volume %s (%s): status=%s but backend missing → marking error",
                vol.get("name"), vol["id"], db_status,
            )
            rpc_client.cast(
                "conductor", "mark_volume_error",
                {"volume_id": vol["id"], "error_message": "Physical storage object missing on node"},
            )
            continue

        # 2. DB says deleting and physical already gone → hard-delete record
        if not physical_exists and db_status == "deleting":
            log.info(
                "[reconcile] volume %s (%s): deleting and backend already gone → removing DB record",
                vol.get("name"), vol["id"],
            )
            rpc_client.cast(
                "conductor", "delete_volume_record",
                {"volume_id": vol["id"]},
            )
            continue

        # 3. DB says error but physical is back → auto-recover to available
        if physical_exists and db_status == "error":
            log.info(
                "[reconcile] volume %s (%s): status=error but backend exists → recovering to available",
                vol.get("name"), vol["id"],
            )
            rpc_client.cast(
                "conductor", "update_volume",
                {"volume_id": vol["id"], "fields": {"status": "available", "error_message": None}},
            )


def reconcile_snapshots(rpc_client) -> None:
    """One pass: compare all DB snapshot records against physical storage."""
    try:
        result = rpc_client.call(
            "conductor", "list_all_snapshots", {}, timeout=15
        )
    except Exception as exc:
        log.warning("[reconcile] could not fetch snapshot list: %s", exc)
        return

    snaps = result.get("snapshots", [])
    if not snaps:
        return

    for snap in snaps:
        db_status = snap.get("status", "").lower()
        if db_status not in ("available", "error", "deleting"):
            continue

        physical_exists = _backend_exists_for_snapshot(snap)

        if not physical_exists and db_status in ("available", "error"):
            log.warning(
                "[reconcile] snapshot %s: status=%s but backend missing → marking error",
                snap["id"], db_status,
            )
            rpc_client.cast(
                "conductor", "mark_snapshot_error",
                {"snapshot_id": snap["id"], "error_message": "Physical snapshot missing on node"},
            )
            continue

        if not physical_exists and db_status == "deleting":
            log.info(
                "[reconcile] snapshot %s: deleting and backend gone → removing DB record",
                snap["id"],
            )
            rpc_client.cast(
                "conductor", "delete_snapshot_record",
                {"snapshot_id": snap["id"]},
            )
            continue

        if physical_exists and db_status == "error":
            log.info(
                "[reconcile] snapshot %s: status=error but backend exists → recovering to available",
                snap["id"],
            )
            rpc_client.cast(
                "conductor", "update_snapshot",
                {"snapshot_id": snap["id"], "fields": {"status": "available", "error_message": None}},
            )


# ═══════════════════════════════════════════════════════════════════════════════
# Daemon loop (mirrors sync_power_states_loop in compute_worker)
# ═══════════════════════════════════════════════════════════════════════════════

def reconcile_loop(rpc_client) -> None:
    """Background thread — runs reconciliation every RECONCILE_INTERVAL seconds."""
    log.info(
        "[reconcile] storage reconciliation loop started (interval=%ds)",
        RECONCILE_INTERVAL,
    )
    while True:
        time.sleep(RECONCILE_INTERVAL)
        reconcile_volumes(rpc_client)
        reconcile_snapshots(rpc_client)


def start_reconcile_thread(rpc_client) -> threading.Thread:
    """Start the background reconciler daemon thread."""
    t = threading.Thread(target=reconcile_loop, args=(rpc_client,), daemon=True)
    t.start()
    return t
