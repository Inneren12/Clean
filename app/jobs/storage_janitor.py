import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.bookings.db_models import OrderPhotoTombstone
from app.infra.storage.backends import StorageBackend
from app.settings import settings

logger = logging.getLogger(__name__)


async def run_storage_janitor(
    session: AsyncSession,
    storage: StorageBackend,
    *,
    batch_size: int | None = None,
    max_attempts: int | None = None,
    retry_interval_seconds: int | None = None,
) -> dict[str, int]:
    """Attempt to clean up orphaned storage objects using tombstones."""

    limit = batch_size if batch_size is not None else settings.storage_delete_batch_size
    max_attempts = max_attempts if max_attempts is not None else settings.storage_delete_max_attempts
    retry_interval_seconds = (
        retry_interval_seconds
        if retry_interval_seconds is not None
        else settings.storage_delete_retry_interval_seconds
    )

    now = datetime.now(timezone.utc)
    retry_cutoff = now - timedelta(seconds=retry_interval_seconds)

    stmt = select(OrderPhotoTombstone).where(OrderPhotoTombstone.processed_at.is_(None))
    if retry_interval_seconds > 0:
        stmt = stmt.where(
            (OrderPhotoTombstone.last_attempt_at.is_(None))
            | (OrderPhotoTombstone.last_attempt_at <= retry_cutoff)
        )
    stmt = stmt.order_by(OrderPhotoTombstone.created_at).limit(limit)
    result = await session.execute(stmt)
    tombstones = list(result.scalars().all())

    processed = 0
    failed = 0

    for tombstone in tombstones:
        try:
            await storage.delete(key=tombstone.storage_key)
        except Exception as exc:  # noqa: BLE001
            tombstone.attempts += 1
            tombstone.last_attempt_at = now
            tombstone.last_error = str(exc)[:255]
            failed += 1
            if tombstone.attempts >= max_attempts:
                # Mark as terminal: set processed_at and update error message
                tombstone.processed_at = now
                tombstone.last_error = f"gave_up_after_{max_attempts}_attempts"
                logger.warning(
                    "order_photo_storage_delete_gave_up",
                    extra={"extra": {"tombstone_id": str(tombstone.tombstone_id), "attempts": tombstone.attempts}},
                )
            continue

        tombstone.processed_at = now
        tombstone.last_error = None
        tombstone.last_attempt_at = now
        processed += 1

    await session.commit()

    logger.info(
        "storage_janitor_cycle",
        extra={"extra": {"processed": processed, "failed": failed}},
    )
    await asyncio.sleep(0)
    return {"processed": processed, "failed": failed}
