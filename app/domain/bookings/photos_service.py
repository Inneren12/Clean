import hashlib
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.bookings.db_models import Booking, OrderPhoto, OrderPhotoTombstone
from app.domain.saas import billing_service
from app.domain.bookings.schemas import PhotoPhase
from app.infra.storage.backends import StorageBackend
from app.settings import settings

logger = logging.getLogger(__name__)

def _allowed_mime_types() -> set[str]:
    return set(settings.order_photo_allowed_mimes)


def _max_bytes() -> int:
    return settings.order_photo_max_bytes


async def fetch_order(
    session: AsyncSession, order_id: str, org_id: uuid.UUID | None = None
) -> Booking:
    stmt = select(Booking).where(
        Booking.booking_id == order_id,
        Booking.org_id == (org_id or settings.default_org_id),
    )
    result = await session.execute(stmt)
    order = result.scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return order


async def update_consent(
    session: AsyncSession, order_id: str, consent: bool, org_id: uuid.UUID | None = None
) -> Booking:
    order = await fetch_order(session, order_id, org_id)
    order.consent_photos = consent
    await session.commit()
    await session.refresh(order)
    return order


def _safe_suffix(original: str | None) -> str:
    if not original:
        return ""
    suffix = Path(original).suffix
    if not suffix:
        return ""
    return suffix if re.match(r"^[A-Za-z0-9_.-]+$", suffix) else ""


def _safe_component(value: str, field: str) -> str:
    if not re.match(r"^[A-Za-z0-9_.-]+$", value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field}",
        )
    return value


def _target_filename(photo_id: str, original_name: str | None) -> str:
    suffix = _safe_suffix(original_name)
    return f"{photo_id}{suffix}"


def _storage_key(org_id: uuid.UUID, order_id: str, photo_id: str, original_name: str | None) -> str:
    safe_order = _safe_component(str(order_id), "order_id")
    safe_org = _safe_component(str(org_id), "org_id")
    safe_photo = _safe_component(str(photo_id), "photo_id")
    suffix = _safe_suffix(original_name)
    return f"orders/{safe_org}/{safe_order}/{safe_photo}{suffix}"


def _validate_content_type(content_type: str | None) -> str:
    if not content_type:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing content type")
    if content_type.lower() not in _allowed_mime_types():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported file type")
    return content_type


async def save_photo(
    session: AsyncSession,
    order: Booking,
    upload: UploadFile,
    phase: PhotoPhase,
    uploaded_by: str,
    org_id: uuid.UUID,
    storage: StorageBackend,
) -> OrderPhoto:
    content_type = _validate_content_type(upload.content_type)
    photo_id = str(uuid.uuid4())
    filename = _target_filename(photo_id, upload.filename)
    key = _storage_key(org_id, order.booking_id, photo_id, upload.filename)
    hasher = hashlib.sha256()
    size = 0

    try:
        async def _stream():
            nonlocal size
            while True:
                chunk = await upload.read(64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > _max_bytes():
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File too large"
                    )
                hasher.update(chunk)
                yield chunk

        await storage.put(key=key, body=_stream(), content_type=content_type)

        photo = OrderPhoto(
            photo_id=photo_id,
            order_id=order.booking_id,
            org_id=org_id,
            phase=phase.value,
            filename=filename,
            original_filename=upload.filename,
            content_type=content_type,
            size_bytes=size,
            sha256=hasher.hexdigest(),
            uploaded_by=uploaded_by,
            storage_provider=settings.order_storage_backend,
            storage_key=key,
        )
        session.add(photo)

        try:
            await session.commit()
            await session.refresh(photo)
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            await storage.delete(key=key)
            logger.exception(
                "order_photo_save_failed_db", extra={"extra": {"order_id": order.booking_id}}
            )
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Upload failed") from exc

        logger.info(
            "order_photo_upload",
            extra={
                "extra": {
                    "order_id": order.booking_id,
                    "photo_id": photo.photo_id,
                    "size_bytes": size,
                    "phase": phase.value,
                    "uploaded_by": uploaded_by,
                }
            },
        )
        return photo
    except HTTPException:
        await storage.delete(key=key)
        raise
    except Exception:  # noqa: BLE001
        await storage.delete(key=key)
        logger.exception("order_photo_save_failed", extra={"extra": {"order_id": order.booking_id}})
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Upload failed")
    finally:
        try:
            await upload.close()
        except Exception:  # noqa: BLE001
            logger.warning(
                "order_photo_upload_close_failed",
                extra={"extra": {"order_id": order.booking_id}},
            )


async def list_photos(
    session: AsyncSession, order_id: str, org_id: uuid.UUID | None = None
) -> list[OrderPhoto]:
    target_org = org_id or settings.default_org_id
    await fetch_order(session, order_id, target_org)
    stmt = (
        select(OrderPhoto)
        .where(OrderPhoto.order_id == order_id, OrderPhoto.org_id == target_org)
        .order_by(OrderPhoto.created_at)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_photo(
    session: AsyncSession, order_id: str, photo_id: str, org_id: uuid.UUID | None = None
) -> OrderPhoto:
    target_org = org_id or settings.default_org_id
    await fetch_order(session, order_id, target_org)
    stmt = select(OrderPhoto).where(
        OrderPhoto.order_id == order_id,
        OrderPhoto.photo_id == photo_id,
        OrderPhoto.org_id == target_org,
    )
    result = await session.execute(stmt)
    photo = result.scalar_one_or_none()
    if photo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found")
    return photo


async def delete_photo(
    session: AsyncSession,
    order_id: str,
    photo_id: str,
    *,
    storage: StorageBackend,
    org_id: uuid.UUID,
    record_usage: bool = False,
) -> OrderPhoto:
    photo = await get_photo(session, order_id, photo_id, org_id)
    key = storage_key_for_photo(photo, org_id)
    tombstone = OrderPhotoTombstone(
        org_id=org_id,
        order_id=order_id,
        photo_id=photo_id,
        storage_key=key,
    )

    try:
        await session.execute(delete(OrderPhoto).where(OrderPhoto.photo_id == photo_id))
        session.add(tombstone)
        if record_usage:
            await billing_service.record_usage_event(
                session,
                org_id,
                metric="storage_bytes",
                quantity=-photo.size_bytes,
                resource_id=photo.photo_id,
            )
        await session.commit()
        await session.refresh(tombstone)
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        logger.exception(
            "order_photo_delete_failed",
            extra={"extra": {"order_id": order_id, "photo_id": photo_id}},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete photo record",
        ) from exc

    try:
        await storage.delete(key=key)
    except Exception as exc:  # noqa: BLE001
        await _record_tombstone_failure(session, tombstone, exc)
        logger.warning(
            "order_photo_storage_delete_deferred",
            extra={
                "extra": {
                    "order_id": order_id,
                    "photo_id": photo_id,
                    "tombstone_id": tombstone.tombstone_id,
                }
            },
        )
        return photo

    await _mark_tombstone_processed(session, tombstone)
    return photo


def allowed_mime_types() -> Iterable[str]:
    return _allowed_mime_types()


def storage_key_for_photo(photo: OrderPhoto, org_id: uuid.UUID) -> str:
    if getattr(photo, "storage_key", None):
        return photo.storage_key
    return _storage_key(org_id, photo.order_id, photo.photo_id, photo.original_filename or photo.filename)


async def _record_tombstone_failure(
    session: AsyncSession, tombstone: OrderPhotoTombstone, exc: Exception
) -> None:
    tombstone.attempts += 1
    tombstone.last_error = str(exc)[:255]
    tombstone.last_attempt_at = datetime.now(timezone.utc)
    await session.commit()


async def _mark_tombstone_processed(
    session: AsyncSession, tombstone: OrderPhotoTombstone
) -> None:
    tombstone.last_attempt_at = datetime.now(timezone.utc)
    tombstone.processed_at = datetime.now(timezone.utc)
    tombstone.last_error = None
    await session.commit()


