from __future__ import annotations
"""
cloudx Image Worker — AMQP Consumer (Image Import Worker)
==============================================================

Consumes messages from RabbitMQ queue ``image``.
Handles asynchronous image import tasks dispatched by the image-api.

Supported handlers:
  - import_image_task: Download image from URL → store locally / in MinIO → mark task/image ACTIVE
"""

import hashlib
import logging
import os
import sys
import time
import uuid
from pathlib import Path

import requests

# Append parent so shared/ is importable
from shared.rpc import RPCConsumer  # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────────
RABBITMQ_URL          = os.getenv("RABBITMQ_URL", "amqp://cloudx:cloudx@127.0.0.1:5672/")
IMAGE_API_URL         = os.getenv("IMAGE_API_URL", "http://127.0.0.1:8002").rstrip("/")
IMAGE_STORE_PATH      = Path(os.getenv("IMAGE_STORE_PATH", "/var/lib/cloudx/images"))
IMAGE_STORAGE_BACKEND = os.getenv("IMAGE_STORAGE_BACKEND", "file").lower()
MINIO_ENDPOINT        = os.getenv("MINIO_ENDPOINT", "http://127.0.0.1:9000")
MINIO_ACCESS_KEY      = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY      = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET          = os.getenv("MINIO_BUCKET", "cloudx-images")
LOG_LEVEL             = os.getenv("LOG_LEVEL", "INFO")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("image-worker")

# ── Storage helpers ───────────────────────────────────────────────────────────

def _save_to_file(stream, object_name: str) -> tuple[int, str]:
    """Save a streaming response body to local filesystem. Returns (size, sha256)."""
    dest = IMAGE_STORE_PATH / object_name
    IMAGE_STORE_PATH.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256()
    size = 0
    with open(dest, "wb") as f:
        for chunk in stream.iter_content(chunk_size=65536):
            if chunk:
                f.write(chunk)
                sha.update(chunk)
                size += len(chunk)
    return size, sha.hexdigest()


def _save_to_minio(stream, object_name: str) -> tuple[int, str]:
    """Save streaming response to MinIO. Returns (size, sha256)."""
    try:
        import boto3
        from botocore.client import Config as BotoConfig

        s3 = boto3.client(
            "s3",
            endpoint_url=MINIO_ENDPOINT,
            aws_access_key_id=MINIO_ACCESS_KEY,
            aws_secret_access_key=MINIO_SECRET_KEY,
            config=BotoConfig(signature_version="s3v4"),
        )
        # Buffer to temp file first (MinIO needs Content-Length for multipart)
        tmp_path = IMAGE_STORE_PATH / f"_tmp_{uuid.uuid4()}"
        IMAGE_STORE_PATH.mkdir(parents=True, exist_ok=True)
        sha = hashlib.sha256()
        size = 0
        with open(tmp_path, "wb") as f:
            for chunk in stream.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    sha.update(chunk)
                    size += len(chunk)
        s3.upload_file(str(tmp_path), MINIO_BUCKET, object_name)
        tmp_path.unlink(missing_ok=True)
        return size, sha.hexdigest()
    except Exception as exc:
        raise RuntimeError(f"MinIO upload failed: {exc}") from exc


def _save_image(stream, object_name: str) -> tuple[int, str]:
    if IMAGE_STORAGE_BACKEND == "minio":
        return _save_to_minio(stream, object_name)
    return _save_to_file(stream, object_name)


# ── image-api HTTP helpers ────────────────────────────────────────────────────

def _patch_task(task_id: str, payload: dict) -> None:
    try:
        requests.patch(f"{IMAGE_API_URL}/v2/tasks/{task_id}", json=payload, timeout=10)
    except Exception as exc:
        log.warning("Failed to update task %s: %s", task_id, exc)


def _patch_image(image_id: str, payload: dict) -> None:
    try:
        requests.patch(f"{IMAGE_API_URL}/images/{image_id}", json=payload, timeout=10)
    except Exception as exc:
        log.warning("Failed to update image %s: %s", image_id, exc)


# ── Handlers ──────────────────────────────────────────────────────────────────

def handle_import_image_task(args: dict) -> None:
    """
    Download an image from a remote URL and store it.

    Expected args:
      task_id    — UUID of the ImageTask record in image-api
      image_id   — UUID of the Image record (pre-created with status='importing')
      import_from — source URL (http/https)
      image_properties — dict with name, disk_format, container_format, visibility, etc.
    """
    task_id      = args.get("task_id")
    image_id     = args.get("image_id")
    import_from  = args.get("import_from", "")
    image_props  = args.get("image_properties", {})

    log.info("Starting image import task=%s image=%s url=%s", task_id, image_id, import_from)

    if not import_from:
        _patch_task(task_id, {"status": "failure", "message": "import_from URL is required"})
        return

    object_name = f"{image_id}.{image_props.get('disk_format', 'qcow2')}"

    # Mark task as processing
    _patch_task(task_id, {"status": "processing"})

    try:
        # Stream-download the image
        resp = requests.get(import_from, stream=True, timeout=300)
        resp.raise_for_status()

        size, checksum = _save_image(resp, object_name)

        # Update image record: set size, checksum, object_name, status=ACTIVE
        _patch_image(image_id, {
            "status": "ACTIVE",
            "size": size,
            "checksum": checksum,
            "object_name": object_name,
            "filename": object_name,
            "storage_backend": IMAGE_STORAGE_BACKEND,
        })

        # Mark task as success
        _patch_task(task_id, {
            "status": "success",
            "result": {"image_id": image_id},
        })
        log.info("Image import task=%s completed: image=%s size=%d checksum=%s",
                 task_id, image_id, size, checksum)

    except requests.exceptions.HTTPError as exc:
        msg = f"HTTP error downloading {import_from}: {exc}"
        log.error("Task %s failed: %s", task_id, msg)
        _patch_task(task_id, {"status": "failure", "message": msg})
        _patch_image(image_id, {"status": "error"})

    except Exception as exc:
        msg = str(exc)
        log.error("Task %s failed unexpectedly: %s", task_id, msg, exc_info=True)
        _patch_task(task_id, {"status": "failure", "message": msg})
        _patch_image(image_id, {"status": "error"})


# ── Handler dispatch ─────────────────────────────────────────────────────────

HANDLERS = {
    "import_image_task": handle_import_image_task,
}

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info("cloudx Image Worker starting (AMQP consumer)")
    log.info("  RabbitMQ:  %s", RABBITMQ_URL.split("@")[-1])
    log.info("  Image API: %s", IMAGE_API_URL)
    log.info("  Storage:   %s", IMAGE_STORAGE_BACKEND)
    log.info("  Handlers:  %s", ", ".join(HANDLERS.keys()))
    log.info("=" * 60)

    # Wait for RabbitMQ
    from shared.rpc import RPCClient
    rpc_test = RPCClient(RABBITMQ_URL)
    for attempt in range(30):
        try:
            rpc_test._ensure_connection()
            log.info("RabbitMQ connection OK")
            break
        except Exception as exc:
            log.warning("Waiting for RabbitMQ (%d/30): %s", attempt + 1, exc)
            time.sleep(2)
    else:
        log.error("Could not connect to RabbitMQ after 30 attempts")
        sys.exit(1)

    # Start background image reconciler thread
    from .reconciler import start_reconcile_thread  # noqa: E402
    start_reconcile_thread()

    consumer = RPCConsumer(
        queue_name="image",
        handlers=HANDLERS,
        rabbitmq_url=RABBITMQ_URL,
    )
    consumer.run()


if __name__ == "__main__":
    main()
