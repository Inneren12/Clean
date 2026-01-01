import hashlib
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Iterable

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.bookings.db_models import Booking, OrderPhoto
from app.domain.saas import billing_service
from app.domain.bookings.schemas import PhotoPhase
from app.infra.storage.backends import LocalStorageBackend, StorageBackend
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


def _target_filename(original_name: str | None) -> str:
    suffix = _safe_suffix(original_name)
    return f"{uuid.uuid4().hex}{suffix}"


def _storage_key(org_id: uuid.UUID, order_id: str, filename: str) -> str:
    safe_order = _safe_component(str(order_id), "order_id")
    safe_org = _safe_component(str(org_id), "org_id")
    safe_filename = _safe_component(filename, "filename")
    return f"orders/{safe_org}/{safe_order}/{safe_filename}"


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
    filename = _target_filename(upload.filename)
    key = _storage_key(org_id, order.booking_id, filename)
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
            order_id=order.booking_id,
            phase=phase.value,
            filename=filename,
            original_filename=upload.filename,
            content_type=content_type,
            size_bytes=size,
            sha256=hasher.hexdigest(),
            uploaded_by=uploaded_by,
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
    key = _storage_key(org_id, order_id, photo.filename)

    try:
        await storage.delete(key=key)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "order_photo_storage_delete_failed",
            extra={"extra": {"order_id": order_id, "photo_id": photo_id}},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete photo from storage",
        ) from exc

    try:
        await session.execute(delete(OrderPhoto).where(OrderPhoto.photo_id == photo_id))
        if record_usage:
            await billing_service.record_usage_event(
                session,
                org_id,
                metric="storage_bytes",
                quantity=-photo.size_bytes,
                resource_id=photo.photo_id,
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "order_photo_delete_failed",
            extra={"extra": {"order_id": order_id, "photo_id": photo_id}},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete photo record",
        ) from exc

    return photo


def allowed_mime_types() -> Iterable[str]:
    return _allowed_mime_types()


def storage_key_for_photo(photo: OrderPhoto, org_id: uuid.UUID) -> str:
    return _storage_key(org_id, photo.order_id, photo.filename)


def validate_local_signature(storage: StorageBackend, key: str, expires_raw: str, signature: str) -> bool:
    if not isinstance(storage, LocalStorageBackend):
        return False
    try:
        expires_at = int(expires_raw)
    except ValueError:
        return False
    return storage.validate_signature(key=key, expires_at=expires_at, signature=signature)
