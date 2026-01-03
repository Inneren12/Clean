from pathlib import Path
from typing import Any

from app.infra.storage.backends import (
    CloudflareImagesStorageBackend,
    InMemoryStorageBackend,
    LocalStorageBackend,
    S3StorageBackend,
    StorageBackend,
)
from app.settings import settings


def _new_backend() -> StorageBackend:
    backend = settings.order_storage_backend.lower()
    if backend == "cf_images":
        backend = "cloudflare_images"
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
    if backend in {"r2", "cloudflare_r2"}:
        if not settings.r2_bucket:
            raise RuntimeError("R2_BUCKET is required when ORDER_STORAGE_BACKEND=r2")
        if not settings.r2_access_key or not settings.r2_secret_key:
            raise RuntimeError("R2_ACCESS_KEY and R2_SECRET_KEY are required for R2 storage")
        return S3StorageBackend(
            bucket=settings.r2_bucket,
            access_key=settings.r2_access_key,
            secret_key=settings.r2_secret_key,
            region=settings.r2_region,
            endpoint=settings.r2_endpoint,
            connect_timeout=settings.s3_connect_timeout_seconds,
            read_timeout=settings.s3_read_timeout_seconds,
            max_attempts=settings.s3_max_attempts,
            max_payload_bytes=settings.order_photo_max_bytes,
            public_base_url=settings.r2_public_base_url,
        )
    if backend == "cloudflare_images":
        if not settings.cf_images_account_id:
            raise RuntimeError(
                "CF_IMAGES_ACCOUNT_ID is required when ORDER_STORAGE_BACKEND=cloudflare_images"
            )
        if not settings.cf_images_api_token:
            raise RuntimeError(
                "CF_IMAGES_API_TOKEN is required when ORDER_STORAGE_BACKEND=cloudflare_images"
            )
        if not settings.cf_images_account_hash:
            raise RuntimeError(
                "CF_IMAGES_ACCOUNT_HASH is required when ORDER_STORAGE_BACKEND=cloudflare_images"
            )
        return CloudflareImagesStorageBackend(
            account_id=settings.cf_images_account_id,
            api_token=settings.cf_images_api_token,
            account_hash=settings.cf_images_account_hash,
            default_variant=settings.cf_images_default_variant,
            signing_key=settings.cf_images_signing_key,
            max_payload_bytes=settings.order_photo_max_bytes,
        )
    if backend == "memory":
        return InMemoryStorageBackend()
    raise RuntimeError(f"Unsupported storage backend: {backend}")


def new_storage_backend() -> StorageBackend:
    return _new_backend()


def resolve_storage_backend(state: Any) -> StorageBackend:
    config_signature = (
        settings.order_storage_backend.lower(),
        Path(settings.order_upload_root).resolve(),
        settings.s3_bucket,
        settings.s3_endpoint,
        settings.r2_bucket,
        settings.r2_endpoint,
        settings.cf_images_account_id,
        settings.cf_images_account_hash,
        settings.cf_images_default_variant,
        settings.cf_images_api_token,
        settings.cf_images_signing_key,
    )
    backend: StorageBackend | None = getattr(state, "storage_backend", None)
    cached_signature = getattr(state, "storage_backend_config", None)
    if backend:
        if cached_signature is None:
            state.storage_backend_config = config_signature
            return backend
        if cached_signature == config_signature:
            return backend
    backend = _new_backend()
    state.storage_backend = backend
    state.storage_backend_config = config_signature
    return backend
