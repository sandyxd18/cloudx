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
  • Volume stuck in "creating" for longer than VOLUME_CREATING_TIMEOUT_SECONDS
    with no backend object → mark error (the create request was lost, e.g.
    with a broker that lost quorum, or the worker died before it started).
  • A volume that is already error with nothing on the backend is left alone, so the reason stored with it
    (from the handler or from the rule above) stays readable.

Scheduling: background thread (daemon) started in block-store-worker main.py,
sleeping RECONCILE_INTERVAL_SECONDS between passes. Default = 3600 (1 hour).
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
from datetime import datetime, timezone

sys = __import__("sys")
os_mod = os  # shadow to keep linter happy

# These imports resolve when the module is imported from the worker package.
from .drivers import lvm, ceph  # noqa: E402

log = logging.getLogger("block-store-reconciler")

RECONCILE_INTERVAL = int(os.getenv("RECONCILE_INTERVAL_SECONDS", "60"))
# LVM objects live on one node: a worker may only judge the ones it owns
BLOCK_STORE_HOST = os.getenv("BLOCK_STORE_HOST") or socket.gethostname()
# A create handler makes the backend object within seconds or minutes. A volume still "creating" after this long with
# nothing on the backend never reached a handler: the message was lost with the broker, or the worker died first.
# Long on purpose, so a slow image conversion is never cut short. 0 switches the check off.
CREATING_TIMEOUT = int(os.getenv("VOLUME_CREATING_TIMEOUT_SECONDS", "3600"))


def _owned_by_other_node(record: dict) -> bool:
    return record.get("backend", "lvm").lower() == "lvm" and record.get("host") not in (None, "", BLOCK_STORE_HOST)

# ── Statuses we are allowed to correct (same pattern as compute_worker) ───────
_RECONCILE_MANAGED_VOLUME_STATUSES = {
    "available",
    "in-use",
    "error",
    "deleting",
    "creating",
}


def _creating_too_long(volume: dict) -> bool:
    """True when the volume has been "creating" for more than CREATING_TIMEOUT seconds; False when it is not known."""
    if CREATING_TIMEOUT <= 0:
        return False
    made = volume.get("created_at")
    if isinstance(made, str):
        try:
            made = datetime.fromisoformat(made)
        except ValueError:
            return False
    if not isinstance(made, datetime):
        return False
    if made.tzinfo is not None:
        made = made.astimezone(timezone.utc).replace(tzinfo=None)  # the column holds naive UTC (datetime.utcnow)
    return (datetime.utcnow() - made).total_seconds() > CREATING_TIMEOUT


def _lv_name(volume_id: str) -> str:
    return f"vol-{volume_id.replace('-', '')[:24]}"


def _snap_lv_name(snapshot_id: str) -> str:
    return f"snap-{snapshot_id.replace('-', '')[:22]}"


def _backend_exists_for_volume(volume: dict) -> bool | None:
    """True/False if the physical storage object for *volume* is/is not present; None if it could not be checked."""
    backend = volume.get("backend", "lvm").lower()
    name = _lv_name(volume["id"])

    if backend == "lvm":
        try:
            return lvm.exists(name)
        except Exception as exc:
            log.warning("[reconcile] cannot check lvm object %s: %s", name, exc)
            return None  # unknown: never treated as 'missing'
    if backend == "ceph":
        try:
            return ceph.exists(name)
        except Exception as exc:
            log.warning("[reconcile] cannot check ceph object %s: %s", name, exc)
            return None  # unknown: never treated as 'missing'
    # file-based / NFS volumes
    path = volume.get("path", "")
    if path:
        return os.path.exists(path)
    return False


def _backend_exists_for_snapshot(snap: dict) -> bool | None:
    """True/False if the physical snapshot object for *snap* is/is not present; None if it could not be checked."""
    backend = snap.get("backend", "lvm").lower()
    name = _snap_lv_name(snap["id"])

    if backend == "lvm":
        try:
            return lvm.exists(name)
        except Exception as exc:
            log.warning("[reconcile] cannot check lvm object %s: %s", name, exc)
            return None  # unknown: never treated as 'missing'
    if backend == "ceph":
        # a Ceph snapshot is pool/image@snap, not an image called snap-<id>: look for it by the path stored when it was
        # made (checking the image name found nothing, so every Ceph snapshot was marked error within a minute)
        path = snap.get("path") or ""
        if not path:
            return None  # nothing to look for: unknown, never treated as 'missing'
        try:
            return ceph.snapshot_exists(path)
        except Exception as exc:
            log.warning("[reconcile] cannot check ceph snapshot %s: %s", path, exc)
            return None  # unknown: never treated as 'missing'
    path = snap.get("path", "")
    if path:
        return os.path.exists(path)
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
        if _owned_by_other_node(vol):
            continue  # another node's LV: its own worker reconciles it

        physical_exists = _backend_exists_for_volume(vol)
        if physical_exists is None:
            continue  # could not check this pass; do not touch the record

        # 0. DB says creating, nothing on the backend and far too long ago: the create request never reached a handler.
        #    A younger one is still the handler's; one whose object exists may be mid-conversion and is left alone.
        if not physical_exists and db_status == "creating":
            if _creating_too_long(vol):
                log.warning(
                    "[reconcile] volume %s (%s): creating for more than %ds with no backend object → marking error",
                    vol.get("name"), vol["id"], CREATING_TIMEOUT,
                )
                rpc_client.cast(
                    "conductor", "mark_volume_error",
                    {"volume_id": vol["id"], "error_message": (
                        f"Creation did not finish within {CREATING_TIMEOUT} s and nothing exists on the backend "
                        "(the request was lost or the worker stopped); delete the volume and create it again"
                    )},
                )
            continue

        # 1a. Already error and still nothing on the backend: leave it. Marking it again on every pass (of every worker)
        #     replaced the reason that was stored with the generic one below.
        if not physical_exists and db_status == "error":
            continue

        # 1. DB says available/in-use but physical missing
        if not physical_exists and db_status in ("available", "in-use"):
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
        if _owned_by_other_node(snap):
            continue

        physical_exists = _backend_exists_for_snapshot(snap)
        if physical_exists is None:
            continue  # could not check this pass; do not touch the record

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
