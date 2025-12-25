import math
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import Select, and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.bookings.db_models import Booking, Team

WORK_START_HOUR = 9
WORK_END_HOUR = 18
SLOT_STEP_MINUTES = 30
BUFFER_MINUTES = 30
BLOCKING_STATUSES = {"PENDING", "CONFIRMED"}


def round_duration_minutes(time_on_site_hours: float) -> int:
    minutes = max(time_on_site_hours, 0) * 60
    rounded_steps = math.ceil(minutes / SLOT_STEP_MINUTES)
    return max(rounded_steps * SLOT_STEP_MINUTES, SLOT_STEP_MINUTES)


def _normalize_datetime(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _day_window(target_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(target_date, time(hour=WORK_START_HOUR, tzinfo=timezone.utc))
    end = datetime.combine(target_date, time(hour=WORK_END_HOUR, tzinfo=timezone.utc))
    return start, end


def _booking_window_filters(day_start: datetime, day_end: datetime) -> Select:
    buffer_delta = timedelta(minutes=BUFFER_MINUTES)
    return select(Booking).where(
        and_(
            Booking.starts_at < day_end + buffer_delta,
            Booking.starts_at > day_start - buffer_delta - timedelta(hours=12),
            Booking.status.in_(BLOCKING_STATUSES),
        )
    )


async def ensure_default_team(session: AsyncSession) -> Team:
    result = await session.execute(select(Team).order_by(Team.team_id).limit(1))
    team = result.scalar_one_or_none()
    if team:
        return team
    team = Team(name="Default Team")
    session.add(team)
    await session.commit()
    await session.refresh(team)
    return team


async def generate_slots(
    target_date: date,
    duration_minutes: int,
    session: AsyncSession,
) -> list[datetime]:
    day_start, day_end = _day_window(target_date)
    duration_delta = timedelta(minutes=duration_minutes)
    buffer_delta = timedelta(minutes=BUFFER_MINUTES)

    bookings_result = await session.execute(_booking_window_filters(day_start, day_end))
    bookings = bookings_result.scalars().all()

    blocked_windows: list[tuple[datetime, datetime]] = []
    for booking in bookings:
        start = _normalize_datetime(booking.starts_at)
        end = start + timedelta(minutes=booking.duration_minutes)
        blocked_windows.append((start - buffer_delta, end + buffer_delta))

    candidate = day_start
    slots: list[datetime] = []
    while candidate + duration_delta <= day_end:
        candidate_end = candidate + duration_delta
        conflict = False
        for blocked_start, blocked_end in blocked_windows:
            if candidate < blocked_end and candidate_end > blocked_start:
                conflict = True
                break
        if not conflict:
            slots.append(candidate)
        candidate += timedelta(minutes=SLOT_STEP_MINUTES)
    return slots


async def is_slot_available(
    starts_at: datetime,
    duration_minutes: int,
    session: AsyncSession,
) -> bool:
    normalized = _normalize_datetime(starts_at)
    slots = await generate_slots(normalized.date(), duration_minutes, session)
    return normalized in slots


async def create_booking(
    starts_at: datetime,
    duration_minutes: int,
    lead_id: str | None,
    session: AsyncSession,
) -> Booking:
    normalized = _normalize_datetime(starts_at)
    if not await is_slot_available(normalized, duration_minutes, session):
        raise ValueError("Requested slot is no longer available")

    team = await ensure_default_team(session)
    booking = Booking(
        team_id=team.team_id,
        lead_id=lead_id,
        starts_at=normalized,
        duration_minutes=duration_minutes,
        status="PENDING",
    )
    session.add(booking)
    await session.commit()
    await session.refresh(booking)
    return booking


async def cleanup_stale_bookings(session: AsyncSession, older_than: timedelta) -> int:
    threshold = datetime.now(tz=timezone.utc) - older_than
    query = select(Booking).where(and_(Booking.status == "PENDING", Booking.created_at < threshold))
    result = await session.execute(query)
    bookings = result.scalars().all()
    deleted = 0
    for booking in bookings:
        await session.delete(booking)
        deleted += 1
    if deleted:
        await session.commit()
    return deleted
