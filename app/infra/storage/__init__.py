from pathlib import Path
from typing import Any

from app.infra.storage.backends import (
    InMemoryStorageBackend,
    LocalStorageBackend,
    S3StorageBackend,
    StorageBackend,
)
from app.settings import settings


def _new_backend() -> StorageBackend:
    backend = settings.order_storage_backend.lower()
    if backend == "local":
        signing_secret = settings.order_photo_signing_secret or settings.auth_secret_key
        return LocalStorageBackend(Path(settings.order_upload_root), signing_secret=signing_secret)
    if backend == "s3":
        if not settings.s3_bucket:
            raise RuntimeError("S3_BUCKET is required when ORDER_STORAGE_BACKEND=s3")
        if not settings.s3_access_key or not settings.s3_secret_key:
            raise RuntimeError("S3_ACCESS_KEY and S3_SECRET_KEY are required for S3 storage")
        return S3StorageBackend(
            bucket=settings.s3_bucket,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            region=settings.s3_region,
            endpoint=settings.s3_endpoint,
            connect_timeout=settings.s3_connect_timeout_seconds,
            read_timeout=settings.s3_read_timeout_seconds,
            max_attempts=settings.s3_max_attempts,
            max_payload_bytes=settings.order_photo_max_bytes,
        )
    if backend == "memory":
        return InMemoryStorageBackend()
    raise RuntimeError(f"Unsupported storage backend: {backend}")


def new_storage_backend() -> StorageBackend:
    return _new_backend()


def resolve_storage_backend(state: Any) -> StorageBackend:
    backend: StorageBackend | None = getattr(state, "storage_backend", None)
    if backend:
        return backend
    backend = _new_backend()
    state.storage_backend = backend
    return backend
