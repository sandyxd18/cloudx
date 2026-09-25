"""
Object Storage Driver Registry
==============================

Provides a uniform interface for different storage backends.
Supported drivers: s3 (MinIO, AWS S3, Ceph RGW, etc.), filesystem.
"""
from __future__ import annotations

from typing import Type

from .base import ObjectStoreDriver  # noqa: F401
from .s3 import S3Driver
from .filesystem import FilesystemDriver

_REGISTRY: dict[str, Type[ObjectStoreDriver]] = {
    "s3": S3Driver,
    "filesystem": FilesystemDriver,
}


def get_driver(name: str) -> Type[ObjectStoreDriver]:
    driver_cls = _REGISTRY.get(name.lower())
    if not driver_cls:
        raise ValueError(f"Unknown object-store driver: {name!r}. Available: {list(_REGISTRY.keys())}")
    return driver_cls


def list_drivers() -> list[str]:
    return list(_REGISTRY.keys())
