import logging
from datetime import datetime, timezone
from math import ceil
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.bookings.db_models import Booking
from app.domain.time_tracking.db_models import WorkTimeEntry

logger = logging.getLogger(__name__)

RUNNING = "RUNNING"
PAUSED = "PAUSED"
FINISHED = "FINISHED"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _serialize(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _parse(dt_str: str | None) -> datetime | None:
    if not dt_str:
        return None
    parsed = datetime.fromisoformat(dt_str)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _active_segment(entry: WorkTimeEntry) -> dict[str, str | None] | None:
    segments = entry.segments or []
    if not segments:
        return None
    last = segments[-1]
    if last.get("end") is None:
        return last
    return None


def _close_active_segment(entry: WorkTimeEntry, now: datetime) -> int:
    segment = _active_segment(entry)
    if not segment:
        return 0
    start = _parse(segment.get("start"))
    elapsed = 0
    if start:
        elapsed = max(0, int((now - start).total_seconds()))
    segment["end"] = _serialize(now)
    segments = list(entry.segments or [])
    segments[-1] = segment
    entry.segments = segments
    entry.total_seconds = (entry.total_seconds or 0) + elapsed
    return elapsed


def _effective_total_seconds(entry: WorkTimeEntry, now: datetime | None = None) -> int:
    now = now or _utcnow()
    total = entry.total_seconds or 0
    if entry.state == RUNNING:
        active = _active_segment(entry)
        if active:
            start = _parse(active.get("start"))
            if start:
                total += max(0, int((now - start).total_seconds()))
    return total


async def _load_entry(session: AsyncSession, booking_id: str) -> WorkTimeEntry | None:
    stmt = select(WorkTimeEntry).where(WorkTimeEntry.booking_id == booking_id).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def _planned_seconds(booking: Booking) -> int | None:
    minutes = booking.planned_minutes or booking.duration_minutes
    if minutes is None:
        return None
    return minutes * 60


def _derive_actual_seconds(
    booking: Booking, entry: WorkTimeEntry | None, now: datetime | None = None
) -> int:
    if entry:
        return _effective_total_seconds(entry, now)
    if booking.actual_seconds is not None:
        return booking.actual_seconds
    if booking.actual_duration_minutes is not None:
        return booking.actual_duration_minutes * 60
    return 0


def _segments_as_models(entry: WorkTimeEntry | None) -> list[dict[str, datetime | None]]:
    if not entry:
        return []
    segments: list[dict[str, datetime | None]] = []
    for raw in entry.segments or []:
        segments.append({"start": _parse(raw.get("start")), "end": _parse(raw.get("end"))})
    return segments


async def start_time_tracking(
    session: AsyncSession, booking_id: str, worker_id: str | None = None, now: datetime | None = None
) -> WorkTimeEntry | None:
    timestamp = now or _utcnow()
    entry = await _load_entry(session, booking_id)
    if entry:
        if entry.state == RUNNING:
            logger.info(
                "time_tracking_start_noop",
                extra={"extra": {"booking_id": booking_id, "worker_id": worker_id, "state": entry.state}},
            )
            return entry
        if entry.state == PAUSED:
            raise ValueError("Time tracking is paused, resume instead")
        raise ValueError("Time tracking already finished")

    booking = await session.get(Booking, booking_id)
    if booking is None:
        return None

    entry = WorkTimeEntry(
        booking_id=booking_id,
        worker_id=worker_id,
        state=RUNNING,
        started_at=timestamp,
        total_seconds=0,
        segments=[{"start": _serialize(timestamp), "end": None}],
    )
    booking.actual_seconds = 0
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    logger.info(
        "time_tracking_start",
        extra={
            "extra": {
                "booking_id": booking_id,
                "worker_id": worker_id,
                "state": entry.state,
                "total_seconds": entry.total_seconds,
            }
        },
    )
    return entry


async def pause_time_tracking(
    session: AsyncSession, booking_id: str, now: datetime | None = None
) -> WorkTimeEntry | None:
    timestamp = now or _utcnow()
    entry = await _load_entry(session, booking_id)
    if entry is None:
        return None
    if entry.state != RUNNING:
        raise ValueError("Time tracking is not running")

    _close_active_segment(entry, timestamp)
    entry.paused_at = timestamp
    entry.state = PAUSED
    booking = await session.get(Booking, booking_id)
    if booking:
        booking.actual_seconds = entry.total_seconds
        if entry.total_seconds and not booking.actual_duration_minutes:
            booking.actual_duration_minutes = ceil(entry.total_seconds / 60)
    await session.commit()
    await session.refresh(entry)
    logger.info(
        "time_tracking_pause",
        extra={
            "extra": {
                "booking_id": booking_id,
                "total_seconds": entry.total_seconds,
                "state": entry.state,
            }
        },
    )
    return entry


async def resume_time_tracking(
    session: AsyncSession, booking_id: str, now: datetime | None = None
) -> WorkTimeEntry | None:
    timestamp = now or _utcnow()
    entry = await _load_entry(session, booking_id)
    if entry is None:
        return None
    if entry.state != PAUSED:
        raise ValueError("Time tracking is not paused")

    segments = list(entry.segments or [])
    segments.append({"start": _serialize(timestamp), "end": None})
    entry.segments = segments
    entry.state = RUNNING
    entry.paused_at = None
    await session.commit()
    await session.refresh(entry)
    logger.info(
        "time_tracking_resume",
        extra={
            "extra": {
                "booking_id": booking_id,
                "total_seconds": entry.total_seconds,
                "state": entry.state,
            }
        },
    )
    return entry


async def finish_time_tracking(
    session: AsyncSession, booking_id: str, now: datetime | None = None
) -> WorkTimeEntry | None:
    timestamp = now or _utcnow()
    entry = await _load_entry(session, booking_id)
    if entry is None:
        return None
    if entry.state not in {RUNNING, PAUSED}:
        raise ValueError("Time tracking already finished")

    if entry.state == RUNNING:
        _close_active_segment(entry, timestamp)
    entry.finished_at = timestamp
    entry.state = FINISHED
    entry.paused_at = None

    booking = await session.get(Booking, booking_id)
    if booking:
        booking.actual_seconds = entry.total_seconds
        if entry.total_seconds:
            booking.actual_duration_minutes = ceil(entry.total_seconds / 60)
    await session.commit()
    await session.refresh(entry)
    logger.info(
        "time_tracking_finish",
        extra={
            "extra": {
                "booking_id": booking_id,
                "total_seconds": entry.total_seconds,
                "state": entry.state,
            }
        },
    )
    return entry


def summarize_order_time(
    booking: Booking, entry: WorkTimeEntry | None, now: datetime | None = None
) -> dict[str, object]:
    planned_seconds = _planned_seconds(booking)
    actual_seconds = _derive_actual_seconds(booking, entry, now)
    delta_seconds = None
    leak_flag = False
    if planned_seconds is not None:
        delta_seconds = actual_seconds - planned_seconds
        leak_flag = actual_seconds > planned_seconds * 1.2

    return {
        "booking_id": booking.booking_id,
        "entry_id": getattr(entry, "entry_id", None),
        "state": getattr(entry, "state", None),
        "started_at": getattr(entry, "started_at", None),
        "paused_at": getattr(entry, "paused_at", None),
        "finished_at": getattr(entry, "finished_at", None),
        "planned_minutes": booking.planned_minutes or booking.duration_minutes,
        "planned_seconds": planned_seconds,
        "total_seconds": _derive_actual_seconds(booking, entry, now),
        "effective_seconds": actual_seconds,
        "delta_seconds": delta_seconds,
        "leak_flag": leak_flag,
        "segments": _segments_as_models(entry),
    }


async def fetch_time_tracking_summary(
    session: AsyncSession, booking_id: str, now: datetime | None = None
) -> dict[str, object] | None:
    booking = await session.get(Booking, booking_id)
    if booking is None:
        return None
    entry = await _load_entry(session, booking_id)
    return summarize_order_time(booking, entry, now)


async def list_time_tracking_summaries(
    session: AsyncSession, booking_ids: Iterable[str], now: datetime | None = None
) -> list[dict[str, object]]:
    if not booking_ids:
        return []
    stmt = (
        select(Booking, WorkTimeEntry)
        .join(WorkTimeEntry, WorkTimeEntry.booking_id == Booking.booking_id, isouter=True)
        .where(Booking.booking_id.in_(list(booking_ids)))
    )
    result = await session.execute(stmt)
    rows = result.all()
    summaries: list[dict[str, object]] = []
    for booking, entry in rows:
        summaries.append(summarize_order_time(booking, entry, now))
    return summaries
