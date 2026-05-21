"""Storage abstraction — swap local ↔ S3 with one env var."""
from __future__ import annotations
import logging, os
from abc import ABC, abstractmethod
from typing import Optional
from flask import current_app

log = logging.getLogger(__name__)


class StorageBackend(ABC):
    @abstractmethod
    def put(self, key: str, body: bytes, content_type: str = "application/octet-stream") -> None: ...
    @abstractmethod
    def get(self, key: str) -> bytes: ...
    @abstractmethod
    def delete(self, key: str) -> None: ...
    @abstractmethod
    def exists(self, key: str) -> bool: ...
    @abstractmethod
    def url(self, key: str) -> str: ...


class LocalStorage(StorageBackend):
    def __init__(self, base_dir: str, base_url: str):
        self.base_dir = os.path.abspath(base_dir)
        self.base_url = base_url.rstrip("/")
        os.makedirs(self.base_dir, exist_ok=True)

    def _path(self, key: str) -> str:
        safe = key.replace("..", "").lstrip("/")
        full = os.path.join(self.base_dir, safe)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        return full

    def put(self, key: str, body: bytes, content_type: str = "application/octet-stream") -> None:
        with open(self._path(key), "wb") as f:
            f.write(body)

    def get(self, key: str) -> bytes:
        with open(self._path(key), "rb") as f:
            return f.read()

    def delete(self, key: str) -> None:
        p = self._path(key)
        if os.path.exists(p):
            os.remove(p)

    def exists(self, key: str) -> bool:
        return os.path.exists(self._path(key))

    def url(self, key: str) -> str:
        return f"{self.base_url}/media/{key}"


class S3Storage(StorageBackend):
    def __init__(self, bucket: str, region: str):
        import boto3
        self.bucket = bucket
        self.region = region
        self.client = boto3.client("s3", region_name=region)
        self.cf_domain = os.environ.get("CLOUDFRONT_DOMAIN", "")

    def put(self, key: str, body: bytes, content_type: str = "application/octet-stream") -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=content_type)

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def url(self, key: str) -> str:
        if self.cf_domain:
            return f"https://{self.cf_domain}/{key}"
        return f"https://{self.bucket}.s3.{self.region}.amazonaws.com/{key}"


_backend: Optional[StorageBackend] = None


def get_storage() -> StorageBackend:
    global _backend
    if _backend is not None:
        return _backend
    cfg = current_app.config
    if cfg["STORAGE_BACKEND"] == "s3":
        _backend = S3Storage(cfg["S3_BUCKET"], cfg["AWS_REGION"])
    else:
        _backend = LocalStorage(cfg["LOCAL_STORAGE_DIR"], cfg["PUBLIC_BASE_URL"])
    return _backend
