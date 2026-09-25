"""
Abstract base class for object-store drivers.
=============================================

All concrete drivers (S3, Filesystem, etc.) must implement these methods.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import BinaryIO


class ObjectStoreDriver(ABC):
    """Uniform interface for object storage backends."""

    name: str = ""

    @abstractmethod
    def __init__(self, config: dict) -> None:
        """Initialise driver with backend-specific config dict."""

    # ── Bucket lifecycle ──────────────────────────────────────────────────────

    @abstractmethod
    def bucket_exists(self, bucket_name: str) -> bool:
        """Return True if the bucket/namespace already exists on the backend."""

    @abstractmethod
    def create_bucket(self, bucket_name: str) -> None:
        """Create the bucket/namespace on the backend."""

    @abstractmethod
    def delete_bucket(self, bucket_name: str) -> None:
        """Delete the bucket/namespace on the backend (must be empty)."""

    # ── Object lifecycle ──────────────────────────────────────────────────────

    @abstractmethod
    def put_object(self, bucket_name: str, key: str, body: BinaryIO, size: int | None = None) -> None:
        """Upload an object to the given bucket/key."""

    @abstractmethod
    def get_object(self, bucket_name: str, key: str) -> bytes:
        """Download an object. Raises FileNotFoundError if missing."""

    @abstractmethod
    def delete_object(self, bucket_name: str, key: str) -> None:
        """Delete an object. Idempotent — does not raise if already absent."""

    @abstractmethod
    def list_objects(self, bucket_name: str, prefix: str = "", max_keys: int = 1000) -> list[dict]:
        """List objects in a bucket.

        Returns list of dicts:
            [{"key": str, "size": int, "last_modified": str (ISO-8601)}, ...]
        """

    @abstractmethod
    def object_exists(self, bucket_name: str, key: str) -> bool:
        """Return True if the object exists."""

    @abstractmethod
    def get_object_info(self, bucket_name: str, key: str) -> dict:
        """Return metadata dict for an object without downloading it.

        Raises FileNotFoundError if the object does not exist.
        """
