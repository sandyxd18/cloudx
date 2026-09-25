"""
S3-compatible object-store driver.

Supports MinIO, AWS S3, Ceph RGW, Cloudflare R2, and any S3-compatible API.
"""
from __future__ import annotations

import logging
import os
from typing import BinaryIO

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from .base import ObjectStoreDriver

log = logging.getLogger("object-store-driver.s3")


class S3Driver(ObjectStoreDriver):
    name = "s3"

    def __init__(self, config: dict) -> None:
        self.endpoint_url = config["endpoint_url"]
        self.access_key = config["access_key"]
        self.secret_key = config["secret_key"]
        self.region = config.get("region", "us-east-1")
        self.use_ssl = config.get("use_ssl", True)
        self.signature_version = config.get("signature_version", "s3v4")

        self._client = boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region,
            use_ssl=self.use_ssl,
            config=Config(signature_version=self.signature_version),
        )

    # ── Bucket ────────────────────────────────────────────────────────────────

    def bucket_exists(self, bucket_name: str) -> bool:
        try:
            self._client.head_bucket(Bucket=bucket_name)
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"404", "NoSuchBucket"}:
                return False
            raise

    def create_bucket(self, bucket_name: str) -> None:
        try:
            self._client.head_bucket(Bucket=bucket_name)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"404", "NoSuchBucket"}:
                kwargs = {"Bucket": bucket_name}
                if self.region != "us-east-1":
                    kwargs["CreateBucketConfiguration"] = {"LocationConstraint": self.region}
                self._client.create_bucket(**kwargs)
            else:
                raise

    def delete_bucket(self, bucket_name: str) -> None:
        self._client.delete_bucket(Bucket=bucket_name)

    # ── Object ────────────────────────────────────────────────────────────────

    def put_object(self, bucket_name: str, key: str, body: BinaryIO, size: int | None = None) -> None:
        extra = {}
        if size is not None:
            extra["ContentLength"] = size
        self._client.upload_fileobj(body, bucket_name, key, ExtraArgs=extra)

    def get_object(self, bucket_name: str, key: str) -> bytes:
        try:
            resp = self._client.get_object(Bucket=bucket_name, Key=key)
            return resp["Body"].read()
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"404", "NoSuchKey"}:
                raise FileNotFoundError(f"s3://{bucket_name}/{key}")
            raise

    def delete_object(self, bucket_name: str, key: str) -> None:
        try:
            self._client.delete_object(Bucket=bucket_name, Key=key)
        except ClientError:
            pass

    def list_objects(self, bucket_name: str, prefix: str = "", max_keys: int = 1000) -> list[dict]:
        paginator = self._client.get_paginator("list_objects_v2")
        result: list[dict] = []
        for page in paginator.paginate(
            Bucket=bucket_name, Prefix=prefix, MaxKeys=max_keys
        ):
            for obj in page.get("Contents", []):
                result.append({
                    "key": obj["Key"],
                    "size": obj["Size"],
                    "last_modified": obj["LastModified"].isoformat(),
                })
        return result

    def object_exists(self, bucket_name: str, key: str) -> bool:
        try:
            self._client.head_object(Bucket=bucket_name, Key=key)
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"404", "NoSuchKey"}:
                return False
            raise

    def get_object_info(self, bucket_name: str, key: str) -> dict:
        try:
            resp = self._client.head_object(Bucket=bucket_name, Key=key)
            return {
                "key": key,
                "size": resp.get("ContentLength", 0),
                "last_modified": resp.get("LastModified", "").isoformat() if resp.get("LastModified") else "",
                "content_type": resp.get("ContentType", "application/octet-stream"),
            }
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"404", "NoSuchKey"}:
                raise FileNotFoundError(f"s3://{bucket_name}/{key}")
            raise
