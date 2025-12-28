import hashlib
import logging
import re
import uuid
from pathlib import Path
from typing import Iterable

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.bookings.db_models import Booking, OrderPhoto
from app.domain.bookings.schemas import PhotoPhase
from app.settings import settings

logger = logging.getLogger(__name__)

def _allowed_mime_types() -> set[str]:
    return set(settings.order_photo_allowed_mimes)


def _max_bytes() -> int:
    return settings.order_photo_max_bytes


def _upload_root() -> Path:
    return Path(settings.order_upload_root)


async def fetch_order(session: AsyncSession, order_id: str) -> Booking:
    order = await session.get(Booking, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return order


async def update_consent(session: AsyncSession, order_id: str, consent: bool) -> Booking:
    order = await fetch_order(session, order_id)
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


def _ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _target_path(order_id: str, original_name: str | None) -> Path:
    suffix = _safe_suffix(original_name)
    filename = f"{uuid.uuid4().hex}{suffix}"
    order_dir = _upload_root() / str(order_id)
    _ensure_directory(order_dir)
    return order_dir / filename


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
) -> OrderPhoto:
    content_type = _validate_content_type(upload.content_type)
    target = _target_path(order.booking_id, upload.filename)
    hasher = hashlib.sha256()
    size = 0

    try:
        with target.open("wb") as buffer:
            while True:
                chunk = await upload.read(64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > _max_bytes():
                    raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File too large")
                hasher.update(chunk)
                buffer.write(chunk)

        photo = OrderPhoto(
            order_id=order.booking_id,
            phase=phase.value,
            filename=target.name,
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
            if target.exists():
                target.unlink(missing_ok=True)
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
        if target.exists():
            target.unlink(missing_ok=True)
        raise
    except Exception:  # noqa: BLE001
        if target.exists():
            target.unlink(missing_ok=True)
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


async def list_photos(session: AsyncSession, order_id: str) -> list[OrderPhoto]:
    await fetch_order(session, order_id)
    stmt = select(OrderPhoto).where(OrderPhoto.order_id == order_id).order_by(OrderPhoto.created_at)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_photo(session: AsyncSession, order_id: str, photo_id: str) -> OrderPhoto:
    stmt = select(OrderPhoto).where(OrderPhoto.order_id == order_id, OrderPhoto.photo_id == photo_id)
    result = await session.execute(stmt)
    photo = result.scalar_one_or_none()
    if photo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found")
    return photo


async def delete_photo(session: AsyncSession, order_id: str, photo_id: str) -> None:
    photo = await get_photo(session, order_id, photo_id)
    path = _upload_root() / order_id / photo.filename
    await session.execute(delete(OrderPhoto).where(OrderPhoto.photo_id == photo_id))
    await session.commit()
    if path.exists():
        path.unlink(missing_ok=True)


def photo_path(photo: OrderPhoto) -> Path:
    return _upload_root() / photo.order_id / photo.filename


def allowed_mime_types() -> Iterable[str]:
    return _allowed_mime_types()
