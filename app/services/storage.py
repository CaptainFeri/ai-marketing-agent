"""Object storage — MinIO, one prefix per tenant (handoff section 4, D8).

Every object key is forced under ``Tenant.storage_prefix`` by
:func:`tenant_key`, which is the only sanctioned way to build one. A media
asset, a consent document or a channel logo written with any other key would
be a tenant-isolation bug that RLS cannot catch, because MinIO has no concept
of the ``tenant_id`` column.

Two backends, the same pattern as :mod:`app.agents.llm` and
:mod:`app.worker.gpu_runtime`:

``S3StorageBackend``
    The real one — MinIO speaks the S3 protocol, so a stock ``boto3`` client
    talks to it with nothing MinIO-specific in this module.
``InMemoryStorageBackend``
    Keeps bytes in a dict. Used by the test suite and by a from-scratch
    ``docker compose up`` before MinIO has a bucket yet.
"""

from __future__ import annotations

import logging
import mimetypes
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from app.core.config import settings

logger = logging.getLogger(__name__)

#: A storage key is a POSIX-style path; these characters are refused so a
#: crafted title or filename cannot climb out of the tenant's prefix.
_UNSAFE_KEY = re.compile(r"(^/|\.\./|//|[\x00-\x1f])")


class StorageError(RuntimeError):
    """The object could not be written or read."""


def tenant_key(storage_prefix: str, *parts: str) -> str:
    """Build an object key that cannot escape ``storage_prefix``.

    Every caller goes through this rather than formatting an f-string, so a
    tenant boundary violation is a single function to audit rather than every
    call site that touches storage.
    """
    if not storage_prefix or _UNSAFE_KEY.search(storage_prefix):
        raise StorageError(f"unsafe storage prefix: {storage_prefix!r}")
    cleaned: list[str] = []
    for part in parts:
        part = str(part).strip("/")
        # ``_UNSAFE_KEY`` catches "../" *inside* a longer segment; a segment
        # that is exactly ".." (or ".") needs its own check, since there is
        # no trailing slash within it for the regex to match against.
        if not part or part in {".", ".."} or _UNSAFE_KEY.search(part):
            raise StorageError(f"unsafe storage key segment: {part!r}")
        cleaned.append(part)
    if not cleaned:
        raise StorageError("a storage key needs at least one segment")
    return "/".join([storage_prefix, *cleaned])


def new_object_name(extension: str) -> str:
    """A collision-proof filename for generated media."""
    return f"{uuid.uuid4().hex}.{extension.lstrip('.')}"


@dataclass(frozen=True)
class StoredObject:
    key: str
    size_bytes: int
    content_type: str


class StorageBackend(Protocol):
    def put(self, key: str, data: bytes, content_type: str | None = None) -> StoredObject: ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...

    def url(self, key: str, expires: timedelta = timedelta(hours=1)) -> str:
        """A URL the panel can load the object from directly.

        A presigned GET on the real backend; a path under a fake scheme in
        the in-memory one, which is never dereferenced outside a test.
        """
        ...

    def list_objects(self, prefix: str = "") -> list[str]:
        """Every key starting with ``prefix``. Used by the daily backup task
        to mirror the whole bucket — nothing else in the platform needs to
        enumerate storage, since every other caller already knows its key."""
        ...


class S3StorageBackend:
    """MinIO (or any S3-compatible store) via boto3."""

    def __init__(
        self,
        *,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        bucket: str | None = None,
        region: str | None = None,
        secure: bool | None = None,
    ) -> None:
        import boto3
        from botocore.config import Config

        endpoint_url = endpoint_url or settings.s3_endpoint_url
        secure = settings.s3_secure if secure is None else secure
        if endpoint_url and not endpoint_url.startswith(("http://", "https://")):
            endpoint_url = f"{'https' if secure else 'http'}://{endpoint_url}"

        self.bucket = bucket or settings.s3_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key or settings.s3_access_key,
            aws_secret_access_key=secret_key or settings.s3_secret_key,
            region_name=region or settings.s3_region,
            # MinIO wants path-style addressing (bucket in the path, not the
            # hostname); virtual-hosted style is the boto3 default and fails
            # against it silently in confusing ways.
            config=Config(s3={"addressing_style": "path"}),
        )

    def put(self, key: str, data: bytes, content_type: str | None = None) -> StoredObject:
        content_type = content_type or mimetypes.guess_type(key)[0] or "application/octet-stream"
        try:
            self._client.put_object(
                Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as StorageError
            raise StorageError(f"could not write {key!r}: {exc}") from exc
        return StoredObject(key=key, size_bytes=len(data), content_type=content_type)

    def get(self, key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"could not read {key!r}: {exc}") from exc

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey"}:
                return False
            raise StorageError(f"could not check {key!r}: {exc}") from exc

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"could not delete {key!r}: {exc}") from exc

    def url(self, key: str, expires: timedelta = timedelta(hours=1)) -> str:
        try:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=int(expires.total_seconds()),
            )
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"could not sign a URL for {key!r}: {exc}") from exc

    def ensure_bucket(self) -> None:
        """Create the bucket if it does not exist yet; idempotent."""
        from botocore.exceptions import ClientError

        try:
            self._client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self._client.create_bucket(Bucket=self.bucket)

    def list_objects(self, prefix: str = "") -> list[str]:
        try:
            keys: list[str] = []
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                keys.extend(entry["Key"] for entry in page.get("Contents", []))
            return keys
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"could not list objects under {prefix!r}: {exc}") from exc


class InMemoryStorageBackend:
    """Keeps everything in a dict. Thread-safe: the GPU worker is single, but
    the test suite exercises this from several sessions concurrently."""

    def __init__(self) -> None:
        self._objects: dict[str, StoredObject] = {}
        self._bytes: dict[str, bytes] = {}
        self._lock = threading.Lock()

    def put(self, key: str, data: bytes, content_type: str | None = None) -> StoredObject:
        content_type = content_type or mimetypes.guess_type(key)[0] or "application/octet-stream"
        obj = StoredObject(key=key, size_bytes=len(data), content_type=content_type)
        with self._lock:
            self._bytes[key] = data
            self._objects[key] = obj
        return obj

    def get(self, key: str) -> bytes:
        with self._lock:
            if key not in self._bytes:
                raise StorageError(f"no such object: {key!r}")
            return self._bytes[key]

    def exists(self, key: str) -> bool:
        with self._lock:
            return key in self._bytes

    def delete(self, key: str) -> None:
        with self._lock:
            self._bytes.pop(key, None)
            self._objects.pop(key, None)

    def url(self, key: str, expires: timedelta = timedelta(hours=1)) -> str:
        return f"memory://{key}"

    def list_objects(self, prefix: str = "") -> list[str]:
        with self._lock:
            return [key for key in self._bytes if key.startswith(prefix)]


_backend: StorageBackend | None = None


def build_backend(name: str | None = None) -> StorageBackend:
    name = name or settings.storage_backend
    if name == "s3":
        return S3StorageBackend()
    return InMemoryStorageBackend()


def get_backend() -> StorageBackend:
    global _backend
    if _backend is None:
        _backend = build_backend()
    return _backend


def set_backend(backend: StorageBackend | None) -> None:
    """Test hook, and how the worker installs a warmed client."""
    global _backend
    _backend = backend
