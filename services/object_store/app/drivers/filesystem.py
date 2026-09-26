"""
Filesystem object-store driver (local disk storage).

Stores objects as plain files on disk under a configurable root directory.
"""
from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from .base import ObjectStoreDriver

log = logging.getLogger("object-store-driver.filesystem")


class FilesystemDriver(ObjectStoreDriver):
    name = "filesystem"

    def __init__(self, config: dict) -> None:
        self.root = Path(config.get("root", "/var/lib/mini-openstack/object-store"))
        self.root.mkdir(parents=True, exist_ok=True)

    def _bucket_path(self, bucket_name: str) -> Path:
        return self.root / bucket_name

    def _object_path(self, bucket_name: str, key: str) -> Path:
        return self._bucket_path(bucket_name) / key

    # ── Bucket ────────────────────────────────────────────────────────────────

    def bucket_exists(self, bucket_name: str) -> bool:
        return self._bucket_path(bucket_name).is_dir()

    def create_bucket(self, bucket_name: str) -> None:
        self._bucket_path(bucket_name).mkdir(parents=True, exist_ok=True)

    def delete_bucket(self, bucket_name: str) -> None:
        bp = self._bucket_path(bucket_name)
        if bp.exists():
            shutil.rmtree(bp, ignore_errors=True)

    # ── Object ────────────────────────────────────────────────────────────────

    def put_object(self, bucket_name: str, key: str, body: BinaryIO, size: int | None = None) -> None:
        dest = self._object_path(bucket_name, key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as out:
            while chunk := body.read(1024 * 1024):
                out.write(chunk)

    def get_object(self, bucket_name: str, key: str) -> bytes:
        path = self._object_path(bucket_name, key)
        if not path.exists():
            raise FileNotFoundError(f"{bucket_name}/{key}")
        return path.read_bytes()

    def delete_object(self, bucket_name: str, key: str) -> None:
        path = self._object_path(bucket_name, key)
        if path.exists():
            path.unlink()

    def list_objects(self, bucket_name: str, prefix: str = "", max_keys: int = 1000) -> list[dict]:
        bp = self._bucket_path(bucket_name)
        if not bp.exists():
            return []

        result: list[dict] = []
        for path in bp.rglob("*"):
            if not path.is_file():
                continue
            rel = str(path.relative_to(bp))
            if prefix and not rel.startswith(prefix):
                continue
            stat = path.stat()
            result.append({
                "key": rel,
                "size": stat.st_size,
                "last_modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })
            if len(result) >= max_keys:
                break
        return result

    def object_exists(self, bucket_name: str, key: str) -> bool:
        return self._object_path(bucket_name, key).exists()

    def get_object_info(self, bucket_name: str, key: str) -> dict:
        path = self._object_path(bucket_name, key)
        if not path.exists():
            raise FileNotFoundError(f"{bucket_name}/{key}")
        stat = path.stat()
        return {
            "key": key,
            "size": stat.st_size,
            "last_modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "content_type": "application/octet-stream",
        }
