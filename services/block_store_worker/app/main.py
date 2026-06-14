"""
cloudx block-store-worker — AMQP Consumer (cinder-volume equivalent)
=====================================================================

Consumes messages from RabbitMQ queue ``block-store``.
Executes actual LVM / Ceph RBD storage operations.
Reports results back to Conductor (DB updates) and publishes events.

Handlers:
  create_volume    — allocate block storage, update DB via conductor
  delete_volume    — remove block storage, delete DB record via conductor
  extend_volume    — resize block storage, update size in DB via conductor
  create_snapshot  — LVM snapshot / Ceph snapshot, update DB via conductor
  delete_snapshot  — remove snapshot, delete DB record via conductor
"""

from __future__ import annotations

import logging
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

# Append parent so shared/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from shared.rpc import RPCClient, RPCConsumer  # noqa: E402

from app.drivers import lvm, ceph  # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────────

RABBITMQ_URL  = os.getenv("RABBITMQ_URL",  "amqp://cloudx:cloudx@rabbitmq:5672/")
IMAGE_API_URL = os.getenv("IMAGE_API_URL", "http://image-api:8000").rstrip("/")
LOG_LEVEL     = os.getenv("LOG_LEVEL", "INFO")

STORAGE_LVM_DRIVER      = os.getenv("STORAGE_LVM_DRIVER", "false").lower() == "true"
STORAGE_CEPH_RBD_DRIVER = os.getenv("STORAGE_CEPH_RBD_DRIVER", "false").lower() == "true"
LVM_VG_NAME  = os.getenv("LVM_VG_NAME", "")
LVM_PV_NAME  = os.getenv("LVM_PV_NAME", "")
CEPH_POOL    = os.getenv("CEPH_POOL", "")
CEPH_CONF    = os.getenv("CEPH_CONF", "/etc/ceph/ceph.conf")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("block-store-worker")

_rpc = RPCClient(RABBITMQ_URL)


# ═══════════════════════════════════════════════════════════════════════════════
# Conductor helpers
# ═══════════════════════════════════════════════════════════════════════════════

def conductor_update_volume(volume_id: str, fields: dict) -> None:
    _rpc.cast("conductor", "update_volume", {"volume_id": volume_id, "fields": fields})


def conductor_update_snapshot(snapshot_id: str, fields: dict) -> None:
    _rpc.cast("conductor", "update_snapshot", {"snapshot_id": snapshot_id, "fields": fields})


def conductor_delete_volume(volume_id: str) -> None:
    _rpc.cast("conductor", "delete_volume_record", {"volume_id": volume_id})


def conductor_delete_snapshot(snapshot_id: str) -> None:
    _rpc.cast("conductor", "delete_snapshot_record", {"snapshot_id": snapshot_id})


def conductor_mark_volume_error(volume_id: str, error: str) -> None:
    _rpc.cast("conductor", "mark_volume_error", {"volume_id": volume_id, "error_message": error})


def conductor_mark_snapshot_error(snapshot_id: str, error: str) -> None:
    _rpc.cast("conductor", "mark_snapshot_error", {"snapshot_id": snapshot_id, "error_message": error})


# ═══════════════════════════════════════════════════════════════════════════════
# LV name helpers (mirror what API uses)
# ═══════════════════════════════════════════════════════════════════════════════

def _lv_name(volume_id: str) -> str:
    return f"vol-{volume_id.replace('-', '')[:24]}"


def _snap_lv_name(snapshot_id: str) -> str:
    return f"snap-{snapshot_id.replace('-', '')[:22]}"


# ═══════════════════════════════════════════════════════════════════════════════
# Image download helper
# ═══════════════════════════════════════════════════════════════════════════════

def download_image(image_ref: str, image_api_url: str) -> tuple[str, dict]:
    """Download an image to a tmp file. Returns (tmp_path, image_dict)."""
    resp = requests.get(f"{image_api_url}/images/{image_ref}", timeout=15)
    resp.raise_for_status()
    image = resp.json()
    tmp = tempfile.NamedTemporaryFile(prefix=".img-", delete=False)
    try:
        with requests.get(f"{image_api_url}/images/{image['id']}/download",
                          timeout=300, stream=True) as dl:
            dl.raise_for_status()
            for chunk in dl.iter_content(chunk_size=4 * 1024 * 1024):
                if chunk:
                    tmp.write(chunk)
    finally:
        tmp.close()
    return tmp.name, image


# ═══════════════════════════════════════════════════════════════════════════════
# Backend operations
# ═══════════════════════════════════════════════════════════════════════════════

def _create_backend(volume_id: str, backend: str, size_gb: int,
                    image_ref: str | None, snapshot_id: str | None,
                    source_volume_id: str | None,
                    image_api_url: str) -> str:
    """Create block storage and return its device path."""
    lv = _lv_name(volume_id)

    if image_ref:
        tmp_path, _ = download_image(image_ref, image_api_url)
        try:
            if backend == "lvm":
                return lvm.create_from_source(lv, tmp_path, size_gb)
            else:
                return ceph.create_from_source(lv, tmp_path, size_gb)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    if snapshot_id:
        snap_lv = _snap_lv_name(snapshot_id)
        # For LVM: restore from snapshot device path stored in DB
        # For Ceph: clone from snapshot
        # We need the snapshot path — it's passed as "snapshot_path" in the message
        snap_path = snapshot_id  # caller passes snapshot_path separately
        if backend == "lvm":
            dev = lvm.create(lv, size_gb)
            subprocess.run(["dd", f"if={snap_path}", f"of={dev}", "bs=4M", "status=none"], check=False)
            return dev
        else:
            snap_img, _, snap_name = snap_path.partition("@")
            snap_img = snap_img.replace("rbd:", "").split("/", 1)[-1]
            return ceph.clone(snap_img, snap_name, lv)

    if source_volume_id:
        # source_path passed in message
        if backend == "lvm":
            # passed as "source_path" in message
            dev = lvm.create(lv, size_gb)
            return dev  # caller fills in source_path dd
        else:
            return ceph.create(lv, size_gb)

    # Blank volume
    if backend == "lvm":
        return lvm.create(lv, size_gb)
    else:
        return ceph.create(lv, size_gb)


# ═══════════════════════════════════════════════════════════════════════════════
# Handlers
# ═══════════════════════════════════════════════════════════════════════════════

def handle_create_volume(args: dict) -> None:
    volume_id      = args["volume_id"]
    backend        = args.get("backend", "lvm")
    size_gb        = int(args["size_gb"])
    image_ref      = args.get("image_ref")
    snapshot_id    = args.get("snapshot_id")
    snapshot_path  = args.get("snapshot_path")     # device path of snapshot
    source_volume_id = args.get("source_volume_id")
    source_path    = args.get("source_path")        # device path of source volume
    bootable       = args.get("bootable", False)
    image_api_url  = args.get("_image_api_url", IMAGE_API_URL)

    log.info("Creating volume %s (%s, %dGB)", volume_id, backend, size_gb)
    lv = _lv_name(volume_id)

    try:
        if image_ref:
            tmp_path, _ = download_image(image_ref, image_api_url)
            try:
                if backend == "lvm":
                    path = lvm.create_from_source(lv, tmp_path, size_gb)
                else:
                    path = ceph.create_from_source(lv, tmp_path, size_gb)
            finally:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

        elif snapshot_id and snapshot_path:
            snap_lv = _snap_lv_name(snapshot_id)
            if backend == "lvm":
                dev = lvm.create(lv, size_gb)
                subprocess.run(["dd", f"if={snapshot_path}", f"of={dev}", "bs=4M", "status=none"], check=False)
                path = dev
            else:
                snap_img, _, snap_name = snapshot_path.partition("@")
                snap_img = snap_img.replace("rbd:", "").split("/", 1)[-1]
                path = ceph.clone(snap_img, snap_name, lv)

        elif source_volume_id and source_path:
            if backend == "lvm":
                dev = lvm.create(lv, size_gb)
                subprocess.run(["dd", f"if={source_path}", f"of={dev}", "bs=4M", "status=none"], check=False)
                path = dev
            else:
                snap_name = f"clone-src-{_snap_lv_name(volume_id)}"
                src_img = source_path.replace("rbd:", "").split("/", 1)[-1]
                path = ceph.clone(src_img, snap_name, lv)

        else:
            # Blank volume
            if backend == "lvm":
                path = lvm.create(lv, size_gb)
            else:
                path = ceph.create(lv, size_gb)

        conductor_update_volume(volume_id, {
            "status": "available",
            "path": path,
            "error_message": None,
        })
        log.info("Volume %s created at %s", volume_id, path)

    except Exception as exc:
        log.error("Failed to create volume %s: %s", volume_id, exc, exc_info=True)
        conductor_mark_volume_error(volume_id, str(exc))


def handle_delete_volume(args: dict) -> None:
    volume_id = args["volume_id"]
    path      = args.get("path", "")
    backend   = args.get("backend", "lvm")
    lv        = _lv_name(volume_id)

    log.info("Deleting volume %s (%s)", volume_id, backend)
    try:
        if backend == "lvm":
            lvm.delete(lv)
        elif backend == "ceph":
            ceph.delete(lv)
    except Exception as exc:
        log.warning("Backend delete for volume %s: %s (continuing with DB cleanup)", volume_id, exc)

    # Always delete the DB record regardless of backend result
    conductor_delete_volume(volume_id)
    log.info("Volume %s deleted", volume_id)


def handle_extend_volume(args: dict) -> None:
    volume_id   = args["volume_id"]
    backend     = args.get("backend", "lvm")
    new_size_gb = int(args["new_size_gb"])
    lv          = _lv_name(volume_id)

    log.info("Extending volume %s to %dGB (%s)", volume_id, new_size_gb, backend)
    try:
        if backend == "lvm":
            lvm.extend(lv, new_size_gb)
        elif backend == "ceph":
            ceph.extend(lv, new_size_gb)
        conductor_update_volume(volume_id, {
            "status": "available",
            "size_gb": new_size_gb,
            "error_message": None,
        })
        log.info("Volume %s extended to %dGB", volume_id, new_size_gb)
    except Exception as exc:
        log.error("Failed to extend volume %s: %s", volume_id, exc, exc_info=True)
        conductor_mark_volume_error(volume_id, str(exc))


def handle_create_snapshot(args: dict) -> None:
    snapshot_id = args["snapshot_id"]
    volume_path = args.get("volume_path", "")
    backend     = args.get("backend", "lvm")
    cow_gb      = int(args.get("cow_gb", 1))
    snap_lv     = _snap_lv_name(snapshot_id)
    # volume_path contains the device path → derive the lv name
    lv = volume_path.split("/")[-1] if volume_path else args.get("name", snap_lv)

    log.info("Creating snapshot %s from %s (%s)", snapshot_id, lv, backend)
    try:
        if backend == "lvm":
            path = lvm.snapshot(lv, snap_lv, cow_gb)
        elif backend == "ceph":
            path = ceph.snapshot(lv, snap_lv, cow_gb)
        else:
            raise ValueError(f"Unknown backend: {backend}")

        conductor_update_snapshot(snapshot_id, {
            "status": "available",
            "path": path,
        })
        log.info("Snapshot %s created at %s", snapshot_id, path)
    except Exception as exc:
        log.error("Failed to create snapshot %s: %s", snapshot_id, exc, exc_info=True)
        conductor_mark_snapshot_error(snapshot_id, str(exc))


def handle_delete_snapshot(args: dict) -> None:
    snapshot_id = args["snapshot_id"]
    backend     = args.get("backend", "lvm")
    snap_lv     = _snap_lv_name(snapshot_id)

    log.info("Deleting snapshot %s (%s)", snapshot_id, backend)
    try:
        if backend == "lvm":
            lvm.delete(snap_lv)
        elif backend == "ceph":
            ceph.delete(snap_lv)
    except Exception as exc:
        log.warning("Backend delete for snapshot %s: %s", snapshot_id, exc)

    conductor_delete_snapshot(snapshot_id)
    log.info("Snapshot %s deleted", snapshot_id)


# ═══════════════════════════════════════════════════════════════════════════════
# Handler dispatch table
# ═══════════════════════════════════════════════════════════════════════════════

HANDLERS = {
    "create_volume":   handle_create_volume,
    "delete_volume":   handle_delete_volume,
    "extend_volume":   handle_extend_volume,
    "create_snapshot": handle_create_snapshot,
    "delete_snapshot": handle_delete_snapshot,
}


# ═══════════════════════════════════════════════════════════════════════════════
# Startup / VG init
# ═══════════════════════════════════════════════════════════════════════════════

def _init_storage() -> None:
    if not STORAGE_LVM_DRIVER and not STORAGE_CEPH_RBD_DRIVER:
        raise RuntimeError("No storage driver enabled — set STORAGE_LVM_DRIVER=true and/or STORAGE_CEPH_RBD_DRIVER=true")

    if STORAGE_LVM_DRIVER:
        if not LVM_VG_NAME:
            raise RuntimeError("STORAGE_LVM_DRIVER=true but LVM_VG_NAME is not set")
        if not LVM_PV_NAME:
            raise RuntimeError("STORAGE_LVM_DRIVER=true but LVM_PV_NAME is not set")
        log.info("Initializing LVM VG '%s'...", LVM_VG_NAME)
        lvm.ensure_vg()
        log.info("LVM VG '%s' ready", LVM_VG_NAME)

    if STORAGE_CEPH_RBD_DRIVER:
        if not CEPH_POOL:
            raise RuntimeError("STORAGE_CEPH_RBD_DRIVER=true but CEPH_POOL is not set")
        log.info("Ceph RBD driver enabled, pool='%s'", CEPH_POOL)


# ═══════════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    log.info("=" * 60)
    log.info("cloudx block-store-worker starting (AMQP consumer)")
    log.info("  RabbitMQ: %s", RABBITMQ_URL.split("@")[-1])
    log.info("  LVM:  %s  VG=%s", STORAGE_LVM_DRIVER, LVM_VG_NAME)
    log.info("  Ceph: %s  Pool=%s", STORAGE_CEPH_RBD_DRIVER, CEPH_POOL)
    log.info("=" * 60)

    # Initialize storage backends (LVM VG, etc.)
    try:
        _init_storage()
    except Exception as exc:
        log.error("Storage initialization failed: %s", exc)
        sys.exit(1)

    # Wait for RabbitMQ
    for attempt in range(30):
        try:
            _rpc._ensure_connection()
            log.info("RabbitMQ connection OK")
            break
        except Exception as exc:
            log.warning("Waiting for RabbitMQ (%d/30): %s", attempt + 1, exc)
            time.sleep(2)
    else:
        log.error("Could not connect to RabbitMQ after 30 attempts")
        sys.exit(1)

    consumer = RPCConsumer(
        queue_name="block-store",
        handlers=HANDLERS,
        rabbitmq_url=RABBITMQ_URL,
    )
    consumer.run()


if __name__ == "__main__":
    main()
