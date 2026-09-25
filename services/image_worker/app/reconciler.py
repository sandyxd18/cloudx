"""
cloudx Image Reconciler (image-worker add-on)
=============================================

Runs periodically to compare the image-api DB against the physical
storage layer (local filesystem / MinIO) and corrects drift.

Drift scenarios this fixes:
  • Image file was deleted outside of our handlers (host reboot,
    manual cleanup, disk failure) → mark image in DB as error.
  • Image stuck in "deleting" but backend already gone → hard-delete DB record.

Scheduling: background thread (daemon) started in image-worker main.py,
sleeping RECONCILE_INTERVAL_SECONDS between passes. Default = 3600 (1 hour).
"""
from __future__ import annotations

import logging
import os
import threading
import time

import requests

log = logging.getLogger("image-reconciler")

RECONCILE_INTERVAL = int(os.getenv("RECONCILE_INTERVAL_SECONDS", "60"))
IMAGE_API_URL = os.getenv("IMAGE_API_URL", "http://127.0.0.1:8002").rstrip("/")
IMAGE_STORE_PATH = os.getenv("IMAGE_STORE_PATH", "/var/lib/mini-openstack/images")
IMAGE_STORAGE_BACKEND = os.getenv("IMAGE_STORAGE_BACKEND", "file").lower()

# MinIO config (only used when backend is minio)
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://127.0.0.1:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "mini-openstack-images")


def _image_exists(object_name: str) -> bool:
    """Return True if the physical image object exists in storage."""
    if IMAGE_STORAGE_BACKEND == "file":
        path = os.path.join(IMAGE_STORE_PATH, object_name)
        return os.path.exists(path)
    if IMAGE_STORAGE_BACKEND == "minio":
        try:
            import boto3
            from botocore.config import Config
            endpoint_url = MINIO_ENDPOINT if MINIO_ENDPOINT.startswith(("http://", "https://")) else f"http://{MINIO_ENDPOINT}"
            client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                aws_access_key_id=MINIO_ACCESS_KEY,
                aws_secret_access_key=MINIO_SECRET_KEY,
                config=Config(signature_version="s3v4"),
            )
            client.head_object(Bucket=MINIO_BUCKET, Key=object_name)
            return True
        except Exception:
            return False
    return False


def _patch_image_status(image_id: str, status: str, error_msg: str | None = None) -> None:
    """Patch image status via image-api HTTP endpoint."""
    payload = {"status": status}
    if error_msg:
        payload["error_message"] = error_msg
    try:
        resp = requests.patch(
            f"{IMAGE_API_URL}/images/{image_id}",
            json=payload,
            timeout=10,
        )
        if resp.status_code not in (200, 204):
            log.warning("Failed to patch image %s: HTTP %s", image_id, resp.status_code)
    except Exception as exc:
        log.warning("Failed to patch image %s: %s", image_id, exc)


def _delete_image_record(image_id: str) -> None:
    """Hard-delete image record via image-api HTTP endpoint."""
    try:
        resp = requests.delete(
            f"{IMAGE_API_URL}/images/{image_id}",
            timeout=10,
        )
        if resp.status_code not in (200, 204, 404):
            log.warning("Failed to delete image %s: HTTP %s", image_id, resp.status_code)
    except Exception as exc:
        log.warning("Failed to delete image %s: %s", image_id, exc)


# ═══════════════════════════════════════════════════════════════════════════════
# Reconciliation pass
# ═══════════════════════════════════════════════════════════════════════════════

def reconcile_images() -> None:
    """One pass: compare all DB image records against physical storage."""
    try:
        resp = requests.get(
            f"{IMAGE_API_URL}/images",
            params={"limit": 10000},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("[reconcile] could not fetch image list: %s", exc)
        return

    images = data.get("images", [])
    if not images:
        return

    for img in images:
        db_status = img.get("status", "").upper()
        object_name = img.get("object_name") or img.get("filename")
        if not object_name:
            continue

        # Only reconcile steady-state images
        if db_status not in ("ACTIVE", "ERROR", "DEACTIVATED", "DELETING"):
            continue

        physical_exists = _image_exists(object_name)

        # 1. DB says ACTIVE/DEACTIVATED but physical missing → mark error
        if not physical_exists and db_status in ("ACTIVE", "DEACTIVATED"):
            log.warning(
                "[reconcile] image %s (%s): status=%s but backend missing → marking error",
                img.get("name"), img["id"], db_status,
            )
            _patch_image_status(
                img["id"], "error",
                "Physical image file missing on storage node",
            )
            continue

        # 2. DB says DELETING and physical already gone → delete record
        if not physical_exists and db_status == "DELETING":
            log.info(
                "[reconcile] image %s (%s): deleting and backend gone → removing DB record",
                img.get("name"), img["id"],
            )
            _delete_image_record(img["id"])
            continue

        # 3. DB says error but physical is back → auto-recover to ACTIVE
        if physical_exists and db_status == "ERROR":
            log.info(
                "[reconcile] image %s (%s): status=error but backend exists → recovering to ACTIVE",
                img.get("name"), img["id"],
            )
            _patch_image_status(img["id"], "ACTIVE")


# ═══════════════════════════════════════════════════════════════════════════════
# Daemon loop
# ═══════════════════════════════════════════════════════════════════════════════

def reconcile_loop() -> None:
    """Background thread — runs reconciliation every RECONCILE_INTERVAL seconds."""
    log.info(
        "[reconcile] image reconciliation loop started (interval=%ds)",
        RECONCILE_INTERVAL,
    )
    while True:
        time.sleep(RECONCILE_INTERVAL)
        reconcile_images()


def start_reconcile_thread() -> threading.Thread:
    """Start the background image reconciler daemon thread."""
    t = threading.Thread(target=reconcile_loop, daemon=True)
    t.start()
    return t
