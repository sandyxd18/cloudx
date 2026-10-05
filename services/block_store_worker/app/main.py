from __future__ import annotations
"""
cloudx block-store-worker — AMQP Consumer (Block Store Volume Worker)
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


import logging
import os
import socket
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

import requests

# Append parent so shared/ is importable
from shared.proc import run  # noqa: E402
from shared.rpc import RPCClient, RPCConsumer, host_queue  # noqa: E402

from .drivers import lvm, ceph, nfs, iscsi  # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────────

RABBITMQ_URL  = os.getenv("RABBITMQ_URL",  "amqp://cloudx:cloudx@127.0.0.1:5672/")
IMAGE_API_URL = os.getenv("IMAGE_API_URL", "http://image-api:8000").rstrip("/")
LOG_LEVEL     = os.getenv("LOG_LEVEL", "INFO")

STORAGE_LVM_DRIVER      = os.getenv("STORAGE_LVM_DRIVER", "false").lower() == "true"
STORAGE_CEPH_RBD_DRIVER = os.getenv("STORAGE_CEPH_RBD_DRIVER", "false").lower() == "true"
STORAGE_NFS_DRIVER      = os.getenv("STORAGE_NFS_DRIVER", "true").lower() == "true"
LVM_VG_NAME  = os.getenv("LVM_VG_NAME", "")
LVM_PV_NAME  = os.getenv("LVM_PV_NAME", "")
CEPH_POOL    = os.getenv("CEPH_POOL", "")
CEPH_CONF    = os.getenv("CEPH_CONF", "/etc/ceph/ceph.conf")

# Name of this storage node. The API addresses volumes to it (volume.host, BLOCK_STORE_LVM_HOSTS).
BLOCK_STORE_HOST = os.getenv("BLOCK_STORE_HOST") or socket.gethostname()
# Export LVM volumes over iSCSI so VMs on other nodes can attach them (needs targetcli-fb, see drivers/iscsi.py)
ISCSI_EXPORT = os.getenv("ISCSI_EXPORT", "false").lower() == "true"

# ── Volume backup → object storage (MinIO/S3) ──────────────────────────────────
MINIO_ENDPOINT   = os.getenv("MINIO_ENDPOINT", "http://127.0.0.1:9000").rstrip("/")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
BACKUP_BUCKET    = os.getenv("BACKUP_BUCKET", "cloudx-backups")

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


def conductor_update_backup(backup_id: str, fields: dict) -> None:
    _rpc.cast("conductor", "update_backup", {"backup_id": backup_id, "fields": fields})


def conductor_delete_backup(backup_id: str) -> None:
    _rpc.cast("conductor", "delete_backup_record", {"backup_id": backup_id})


def conductor_mark_backup_error(backup_id: str, error: str) -> None:
    _rpc.cast("conductor", "mark_backup_error", {"backup_id": backup_id, "error_message": error})


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
            except OSError as exc:
                log.warning("could not remove temp file %s: %s", tmp_path, exc)

    if snapshot_id:
        snap_lv = _snap_lv_name(snapshot_id)
        # For LVM: restore from snapshot device path stored in DB
        # For Ceph: clone from snapshot
        # We need the snapshot path — it's passed as "snapshot_path" in the message
        snap_path = snapshot_id  # caller passes snapshot_path separately
        if backend == "lvm":
            dev = lvm.create(lv, size_gb)
            run(["dd", f"if={snap_path}", f"of={dev}", "bs=4M", "status=none"], timeout=None)
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
    lvm_type       = args.get("lvm_type", "default")  # default | thin
    max_iops       = args.get("max_iops")           # QoS: from volume type
    max_throughput = args.get("max_throughput")     # QoS: from volume type (MB/s)

    log.info("Creating volume %s (%s, %dGB)", volume_id, backend, size_gb)
    lv = _lv_name(volume_id)

    try:
        if image_ref:
            tmp_path, _ = download_image(image_ref, image_api_url)
            try:
                if backend == "lvm":
                    path = lvm.create_from_source(lv, tmp_path, size_gb)
                elif backend == "ceph":
                    path = ceph.create_from_source(lv, tmp_path, size_gb)
                else:
                    path = nfs.create_from_source(lv, tmp_path, size_gb)
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError as exc:
                    log.warning("could not remove temp file %s: %s", tmp_path, exc)

        elif snapshot_id and snapshot_path:
            snap_lv = _snap_lv_name(snapshot_id)
            if backend == "lvm":
                dev = lvm.create(lv, size_gb)
                run(["dd", f"if={snapshot_path}", f"of={dev}", "bs=4M", "status=none"], timeout=None)
                path = dev
            elif backend == "ceph":
                snap_img, _, snap_name = snapshot_path.partition("@")
                snap_img = snap_img.replace("rbd:", "").split("/", 1)[-1]
                path = ceph.clone(snap_img, snap_name, lv)
            else:
                path = nfs.clone(snapshot_path, snap_lv, lv)

        elif source_volume_id and source_path:
            if backend == "lvm":
                dev = lvm.create(lv, size_gb)
                run(["dd", f"if={source_path}", f"of={dev}", "bs=4M", "status=none"], timeout=None)
                path = dev
            elif backend == "ceph":
                snap_name = f"clone-src-{_snap_lv_name(volume_id)}"
                src_img = source_path.replace("rbd:", "").split("/", 1)[-1]
                path = ceph.clone(src_img, snap_name, lv)
            else:
                path = nfs.clone(source_path, "", lv)

        else:
            # Blank volume
            if backend == "lvm":
                path = lvm.create(lv, size_gb, lvm_type=lvm_type)
            elif backend == "ceph":
                path = ceph.create(lv, size_gb)
            else:
                path = nfs.create(lv, size_gb)

        # ── Apply Ceph QoS if volume type specifies limits ─────────────────
        if backend == "ceph" and (max_iops or max_throughput):
            try:
                ceph.set_qos(lv, max_iops=max_iops, max_throughput_mbps=max_throughput)
            except Exception as qos_exc:
                log.warning("Ceph QoS apply failed for volume %s: %s (volume is still usable)", volume_id, qos_exc)

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
            if ISCSI_EXPORT:
                iscsi.remove(lv)  # an exported LV is busy until its target is gone
            lvm.delete(lv)
        elif backend == "ceph":
            ceph.delete(lv)
        else:
            nfs.delete(lv)
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
        else:
            nfs.extend(lv, new_size_gb)
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
            path = nfs.snapshot(lv, snap_lv, cow_gb)

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
        else:
            nfs.delete(snap_lv)
    except Exception as exc:
        log.warning("Backend delete for snapshot %s: %s", snapshot_id, exc)

    conductor_delete_snapshot(snapshot_id)
    log.info("Snapshot %s deleted", snapshot_id)


def handle_create_cgsnapshot(args: dict) -> None:
    """
    Create snapshots for all volumes in a consistency group.

    Strategy:
    - LVM: loop over each volume and create an LVM snapshot sequentially.
    - Ceph: loop similarly (rbd group snapshot would be ideal but requires
      the group feature enabled; sequential is safe for lab use).

    On failure, the CGSnapshot record is marked as 'error' via conductor.
    Individual volume snapshots that already completed are preserved.
    """
    cgsnapshot_id = args["cgsnapshot_id"]
    volumes       = args.get("volumes", [])

    log.info("Creating CGSnapshot %s for %d volumes", cgsnapshot_id, len(volumes))

    failed = []
    created_snap_ids = []

    for vol in volumes:
        volume_id  = vol["volume_id"]
        vol_name   = vol.get("name", volume_id)
        backend    = vol.get("backend", "lvm")
        size_gb    = int(vol.get("size_gb", 1))
        path       = vol.get("path", "")

        # Derive a unique snap name embedding both the CG snap ID and the volume ID
        snap_suffix = f"{cgsnapshot_id[:8]}-{volume_id[:8]}"
        snap_name   = f"cgsnap-{snap_suffix}"
        snap_lv     = f"snap-{snap_suffix.replace('-', '')[:20]}"

        # LV name from the volume path or standard naming
        source_lv = path.split("/")[-1] if path else _lv_name(volume_id)

        log.info("  snapshotting vol %s (%s) as %s", vol_name, backend, snap_name)
        try:
            if backend == "lvm":
                snap_path = lvm.snapshot(source_lv, snap_lv, max(1, size_gb // 10))
            elif backend == "ceph":
                snap_path = ceph.snapshot(source_lv, snap_lv, 1)
            else:
                raise ValueError(f"Unknown backend: {backend}")

            # Record this individual snapshot in DB via conductor
            _rpc.cast("conductor", "create_snapshot_record", {
                "snapshot_id": f"{cgsnapshot_id}-{volume_id}",
                "name":        snap_name,
                "volume_id":   volume_id,
                "size_gb":     size_gb,
                "status":      "available",
                "path":        snap_path,
                "backend":     backend,
            })
            created_snap_ids.append(snap_name)
        except Exception as exc:
            log.error("  Failed to snapshot vol %s: %s", vol_name, exc)
            failed.append(volume_id)

    # Update CGSnapshot status
    final_status = "error" if failed else "available"
    _rpc.cast("conductor", "update_cgsnapshot", {
        "cgsnapshot_id": cgsnapshot_id,
        "status":        final_status,
    })
    if failed:
        log.warning("CGSnapshot %s completed with errors for volumes: %s", cgsnapshot_id, failed)
    else:
        log.info("CGSnapshot %s completed successfully (%d snapshots)", cgsnapshot_id, len(created_snap_ids))



def handle_migrate_volume(args: dict) -> None:
    """
    Migrate a volume to a different backend/host.

    Strategy (cold migration only — volume must be 'available'):
      1. Create a new volume on the target backend.
      2. Copy data from source to destination using qemu-img (LVM→LVM),
         rbd export/import (Ceph→Ceph), or qemu-img convert (cross-backend).
      3. Update DB via conductor to point to new path/backend.
      4. Delete the old volume from the source backend.

    On any failure, the volume is reset to 'available' with an error note,
    so the original data remains intact.
    """
    volume_id   = args["volume_id"]
    volume_name = args.get("name", volume_id)
    source_path = args.get("path", "")
    backend     = args.get("backend", "lvm")
    size_gb     = int(args.get("size_gb", 1))
    target_host = args.get("target_host", "")
    force_copy  = args.get("force_host_copy", False)

    # Parse target backend from the host string (e.g. "block_store@ceph#ceph" → "ceph")
    # Format is "service@driver#pool" (OpenStack convention)
    target_backend = backend  # default: same backend
    if "#" in target_host:
        target_backend = target_host.split("#")[-1].lower()
    elif "@" in target_host:
        target_backend = target_host.split("@")[-1].lower()

    log.info(
        "Migrating volume %s (%s, %d GB): %s → host=%s (backend=%s)",
        volume_id, volume_name, size_gb, backend, target_host, target_backend
    )

    source_lv = _lv_name(volume_id)
    # Destination LV uses same naming (same volume_id, different VG/pool conceptually)
    dest_lv   = _lv_name(volume_id)

    try:
        new_path = ""

        # ── LVM → LVM (same node, different VG): use qemu-img dd ──────────
        if backend == "lvm" and target_backend == "lvm":
            # Create new LV on the same VG (represents migration to a fresh thin pool)
            dest_lv_name = f"vol-mig-{volume_id.replace('-', '')[:20]}"
            lvm.create(dest_lv_name, size_gb)
            new_path = f"/dev/{LVM_VG_NAME}/{dest_lv_name}"
            # Copy using dd via subprocess
            source_dev = f"/dev/{LVM_VG_NAME}/{source_lv}"
            dest_dev   = new_path
            log.info("Copying %s → %s via dd", source_dev, dest_dev)
            run(["dd", f"if={source_dev}", f"of={dest_dev}", "bs=4M", "status=none"], timeout=3600)
            # Delete old LV
            try:
                lvm.delete(source_lv)
            except Exception as del_exc:
                log.warning("Could not delete source LV %s: %s", source_lv, del_exc)

        # ── Ceph → Ceph: use rbd export/import with new name ──────────────
        elif backend == "ceph" and target_backend == "ceph":
            dest_rbd_name = f"vol-mig-{volume_id.replace('-', '')[:20]}"
            log.info("Ceph rbd copy: %s → %s", source_lv, dest_rbd_name)
            run(["rbd", "copy", "--pool", CEPH_POOL, source_lv, dest_rbd_name], timeout=3600)
            new_path = f"rbd:{CEPH_POOL}/{dest_rbd_name}"
            # Delete old image (non-fatal)
            try:
                ceph.delete(source_lv)
            except Exception as del_exc:
                log.warning("Could not delete source Ceph image %s: %s", source_lv, del_exc)

        # ── Cross-backend: export to temp file, import on destination ─────
        else:
            log.info("Cross-backend migration %s → %s via temp file", backend, target_backend)
            with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as tmp:
                tmp_path = tmp.name
            try:
                # Export from source
                if backend == "lvm":
                    source_dev = f"/dev/{LVM_VG_NAME}/{source_lv}"
                    run(["qemu-img", "convert", "-f", "raw", "-O", "raw", source_dev, tmp_path], timeout=3600)
                elif backend == "ceph":
                    run(["rbd", "export", "--pool", CEPH_POOL, source_lv, tmp_path], timeout=3600)

                # Import to destination
                if target_backend == "lvm":
                    dest_lv_name = f"vol-mig-{volume_id.replace('-', '')[:20]}"
                    lvm.create(dest_lv_name, size_gb)
                    dest_dev = f"/dev/{LVM_VG_NAME}/{dest_lv_name}"
                    run(["qemu-img", "convert", "-f", "raw", "-O", "raw", tmp_path, dest_dev], timeout=3600)
                    new_path = dest_dev
                elif target_backend == "ceph":
                    dest_rbd_name = f"vol-mig-{volume_id.replace('-', '')[:20]}"
                    run(["rbd", "import", "--pool", CEPH_POOL, tmp_path, dest_rbd_name], timeout=3600)
                    new_path = f"rbd:{CEPH_POOL}/{dest_rbd_name}"
            finally:
                Path(tmp_path).unlink(missing_ok=True)

        # Update DB: new path, new backend, status = available
        conductor_update_volume(volume_id, {
            "status":  "available",
            "path":    new_path,
            "backend": target_backend,
            "error_message": None,
        })
        log.info("Volume %s migrated successfully to %s (path=%s)", volume_id, target_host, new_path)

    except Exception as exc:
        log.error("Migration failed for volume %s: %s", volume_id, exc, exc_info=True)
        # Reset to available so user can retry — data is still on original backend
        conductor_update_volume(volume_id, {
            "status":        "available",
            "error_message": f"Migration to {target_host} failed: {exc}",
        })


# ═══════════════════════════════════════════════════════════════════════════════
# Volume backup → MinIO/S3 (Block Store S3 backup driver)
# ═══════════════════════════════════════════════════════════════════════════════

def _mc_env() -> dict[str, str]:
    """mc reads the `cloudx` alias from MC_HOST_cloudx, so the credentials never appear on a command line (`ps`)."""
    endpoint = MINIO_ENDPOINT if "://" in MINIO_ENDPOINT else f"http://{MINIO_ENDPOINT}"
    scheme, rest = endpoint.split("://", 1)
    creds = f"{quote(MINIO_ACCESS_KEY, safe='')}:{quote(MINIO_SECRET_KEY, safe='')}"
    return {**os.environ, "MC_HOST_cloudx": f"{scheme}://{creds}@{rest}"}


def _mc(args: list[str], **kwargs):
    return run(["mc", *args], env=_mc_env(), secrets=[MINIO_SECRET_KEY, quote(MINIO_SECRET_KEY, safe="")], **kwargs)


def _mc_alias() -> None:
    """Make sure the backup bucket exists (the `cloudx` mc alias itself comes from the environment, see _mc_env)."""
    _mc(["mb", "--ignore-existing", f"cloudx/{BACKUP_BUCKET}"], check=False)


def _export_volume_to_file(volume_path: str, backend: str, dest_file: str) -> None:
    """Export a volume's raw data to a local file using the right backend tool."""
    if backend == "ceph":
        # volume_path like rbd:pool/image → strip prefix to pool/image
        rbd_name = volume_path.replace("rbd:", "").split("/", 1)[-1]
        run(["rbd", "export", "--pool", CEPH_POOL, rbd_name, dest_file], timeout=7200)
    else:  # lvm (volume_path is a device like /dev/vg/vol-xxx)
        run(["qemu-img", "convert", "-f", "raw", "-O", "raw", volume_path, dest_file], timeout=7200)


def _import_file_to_volume(src_file: str, backend: str, volume_id: str, size_gb: int) -> str:
    """Import a local raw file into a freshly created volume; return its path."""
    if backend == "ceph":
        rbd_name = _lv_name(volume_id)
        ceph.create_from_source(rbd_name, src_file, size_gb)
        return f"rbd:{CEPH_POOL}/{rbd_name}"
    else:
        lv = _lv_name(volume_id)
        lvm.create(lv, size_gb)
        dev = f"/dev/{LVM_VG_NAME}/{lv}"
        run(["qemu-img", "convert", "-f", "raw", "-O", "raw", src_file, dev], timeout=7200)
        return dev


def handle_backup_volume(args: dict) -> None:
    """Export a volume and upload it to the backup bucket in MinIO."""
    backup_id   = args["backup_id"]
    volume_path = args.get("volume_path", "")
    backend     = args.get("backend", "lvm")
    size_gb     = int(args.get("size_gb", 1))
    object_key  = args["object_key"]

    log.info("Backing up volume %s (%s) → %s/%s", args.get("volume_id"), backend, BACKUP_BUCKET, object_key)
    tmp = None
    try:
        _mc_alias()
        with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as f:
            tmp = f.name
        _export_volume_to_file(volume_path, backend, tmp)
        _mc(["cp", tmp, f"cloudx/{BACKUP_BUCKET}/{object_key}"], timeout=7200)
        conductor_update_backup(backup_id, {"status": "available", "object_key": object_key})
        log.info("Backup %s uploaded to %s/%s", backup_id, BACKUP_BUCKET, object_key)
    except Exception as exc:
        log.error("Backup %s failed: %s", backup_id, exc, exc_info=True)
        conductor_mark_backup_error(backup_id, str(exc))
    finally:
        if tmp:
            Path(tmp).unlink(missing_ok=True)


def handle_restore_backup(args: dict) -> None:
    """Download a backup object from MinIO and import it into a new volume."""
    backup_id  = args["backup_id"]
    object_key = args["object_key"]
    backend    = args.get("backend", "lvm")
    volume_id  = args["volume_id"]
    size_gb    = int(args.get("size_gb", 1))

    log.info("Restoring backup %s → volume %s (%s)", backup_id, volume_id, backend)
    tmp = None
    try:
        _mc_alias()
        with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as f:
            tmp = f.name
        _mc(["cp", f"cloudx/{BACKUP_BUCKET}/{object_key}", tmp], timeout=7200)
        new_path = _import_file_to_volume(tmp, backend, volume_id, size_gb)
        conductor_update_volume(volume_id, {"status": "available", "path": new_path})
        log.info("Backup %s restored into volume %s at %s", backup_id, volume_id, new_path)
    except Exception as exc:
        log.error("Restore of backup %s failed: %s", backup_id, exc, exc_info=True)
        conductor_mark_volume_error(volume_id, f"restore failed: {exc}")
    finally:
        if tmp:
            Path(tmp).unlink(missing_ok=True)


def handle_delete_backup(args: dict) -> None:
    """Remove a backup object from MinIO and delete its DB record."""
    backup_id  = args["backup_id"]
    object_key = args.get("object_key")
    log.info("Deleting backup %s (%s)", backup_id, object_key)
    try:
        if object_key:
            _mc_alias()
            _mc(["rm", f"cloudx/{BACKUP_BUCKET}/{object_key}"], check=False)
    except Exception as exc:
        log.warning("Backup object delete for %s: %s", backup_id, exc)
    conductor_delete_backup(backup_id)


# ═══════════════════════════════════════════════════════════════════════════════
# Handler dispatch table
# ═══════════════════════════════════════════════════════════════════════════════

def handle_export_volume(args: dict) -> dict:
    """RPC call from a compute-worker: let its iSCSI initiator log in to the volume. Returns the connection info."""
    if not ISCSI_EXPORT:
        raise RuntimeError("iSCSI export is not enabled on this storage node (ISCSI_EXPORT=true)")
    lv = _lv_name(args["volume_id"])
    info = iscsi.export(lv, lvm.device_path(lv), args["initiator_iqn"])
    log.info("Exported %s to %s", lv, args["initiator_iqn"])
    return info


def handle_unexport_volume(args: dict) -> dict:
    """RPC call from a compute-worker that logged out: revoke its access."""
    if not ISCSI_EXPORT:
        return {}
    lv = _lv_name(args["volume_id"])
    iscsi.unexport(lv, args["initiator_iqn"])
    log.info("Unexported %s from %s", lv, args["initiator_iqn"])
    return {}


HANDLERS = {
    "export_volume":    handle_export_volume,
    "unexport_volume":  handle_unexport_volume,
    "create_volume":    handle_create_volume,
    "delete_volume":    handle_delete_volume,
    "extend_volume":    handle_extend_volume,
    "migrate_volume":   handle_migrate_volume,
    "create_snapshot":  handle_create_snapshot,
    "delete_snapshot":  handle_delete_snapshot,
    "create_cgsnapshot": handle_create_cgsnapshot,
    "backup_volume":    handle_backup_volume,
    "restore_backup":   handle_restore_backup,
    "delete_backup":    handle_delete_backup,
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
    log.info("  Host: %s  iSCSI export: %s", BLOCK_STORE_HOST, ISCSI_EXPORT)
    log.info("=" * 60)

    # Initialize storage backends (LVM VG, etc.).
    # In a lab/VM environment this may fail when loop devices are not yet ready;
    # keep the worker alive so it can still process record-level RPCs such as
    # delete_volume_record via conductor.
    try:
        _init_storage()
    except Exception as exc:
        log.warning("Storage initialization failed: %s. Continuing anyway (backend ops may fail).", exc)

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

    # Start background storage reconciler thread (same interval as compute_worker)
    from .reconciler import start_reconcile_thread  # noqa: E402
    start_reconcile_thread(_rpc)

    # Own queue for what is addressed to this node (its LVM volumes) + the shared queue for the rest
    # (volumes without a host, backups in object storage).
    consumer = RPCConsumer(
        queue_name=host_queue("block-store", BLOCK_STORE_HOST),
        extra_queues=("block-store",),
        handlers=HANDLERS,
        rabbitmq_url=RABBITMQ_URL,
    )
    consumer.run()


if __name__ == "__main__":
    main()
