import math
from collections import defaultdict
from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.analytics.db_models import EventLog
from app.domain.bookings.db_models import Booking
from app.domain.leads.db_models import Lead


class EventType(StrEnum):
    lead_created = "lead_created"
    booking_created = "booking_created"
    booking_confirmed = "booking_confirmed"
    job_completed = "job_completed"


def _normalize_dt(value: datetime | None, default: datetime | None = None) -> datetime:
    if value is None:
        if default is None:
            raise ValueError("default datetime is required when value is None")
        return default
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def log_event(
    session: AsyncSession,
    *,
    event_type: EventType,
    lead: Lead | None = None,
    booking: Booking | None = None,
    estimated_revenue_cents: int | None = None,
    estimated_duration_minutes: int | None = None,
    actual_duration_minutes: int | None = None,
    occurred_at: datetime | None = None,
) -> EventLog:
    timestamp = _normalize_dt(occurred_at, default=datetime.now(tz=timezone.utc))
    event = EventLog(
        event_type=event_type.value,
        lead_id=lead.lead_id if lead else None,
        booking_id=booking.booking_id if booking else None,
        estimated_revenue_cents=estimated_revenue_cents,
        estimated_duration_minutes=estimated_duration_minutes,
        actual_duration_minutes=actual_duration_minutes,
        utm_source=getattr(lead, "utm_source", None),
        utm_medium=getattr(lead, "utm_medium", None),
        utm_campaign=getattr(lead, "utm_campaign", None),
        utm_term=getattr(lead, "utm_term", None),
        utm_content=getattr(lead, "utm_content", None),
        referrer=getattr(lead, "referrer", None),
        occurred_at=timestamp,
    )
    session.add(event)
    await session.flush()
    return event


async def conversion_counts(
    session: AsyncSession, start: datetime, end: datetime
) -> dict[EventType, int]:
    stmt: Select = (
        select(EventLog.event_type, func.count())
        .where(EventLog.occurred_at >= start, EventLog.occurred_at <= end)
        .group_by(EventLog.event_type)
    )
    result = await session.execute(stmt)
    counts_raw = defaultdict(int)
    for event_type, count in result.all():
        try:
            counts_raw[EventType(event_type)] = int(count)
        except ValueError:
            continue
    return counts_raw


async def average_revenue_cents(
    session: AsyncSession, start: datetime, end: datetime
) -> float | None:
    stmt = select(func.avg(EventLog.estimated_revenue_cents)).where(
        EventLog.event_type.in_(
            [EventType.booking_confirmed.value, EventType.job_completed.value]
        ),
        EventLog.occurred_at >= start,
        EventLog.occurred_at <= end,
        EventLog.estimated_revenue_cents.isnot(None),
    )
    result = await session.execute(stmt)
    avg_value = result.scalar_one_or_none()
    return float(avg_value) if avg_value is not None else None


async def duration_accuracy(
    session: AsyncSession, start: datetime, end: datetime
) -> tuple[float | None, float | None, float | None, int]:
    stmt = (
        select(Booking.duration_minutes, Booking.actual_duration_minutes)
        .join(EventLog, EventLog.booking_id == Booking.booking_id)
        .where(
            EventLog.event_type == EventType.job_completed.value,
            EventLog.occurred_at >= start,
            EventLog.occurred_at <= end,
            Booking.actual_duration_minutes.isnot(None),
        )
    )
    result = await session.execute(stmt)
    rows = result.all()
    if not rows:
        return None, None, None, 0

    estimated_values: list[int] = []
    actual_values: list[int] = []
    deltas: list[int] = []
    for estimated, actual in rows:
        if estimated is None or actual is None:
            continue
        estimated_values.append(int(estimated))
        actual_values.append(int(actual))
        deltas.append(int(actual) - int(estimated))

    if not estimated_values or not actual_values:
        return None, None, None, 0

    avg_estimated = sum(estimated_values) / len(estimated_values)
    avg_actual = sum(actual_values) / len(actual_values)
    avg_delta = sum(deltas) / len(deltas) if deltas else 0.0
    return (
        float(round(avg_estimated, 2)),
        float(round(avg_actual, 2)),
        float(round(avg_delta, 2)),
        len(actual_values),
    )


def estimated_revenue_from_lead(lead: Lead | None) -> int | None:
    if lead is None:
        return None
    snapshot = getattr(lead, "estimate_snapshot", None) or {}
    total_before_tax = snapshot.get("total_before_tax")
    if total_before_tax is None:
        return None
    try:
        return int(round(float(total_before_tax) * 100))
    except (TypeError, ValueError):
        return None


def estimated_duration_from_booking(booking: Booking | None) -> int | None:
    if booking is None:
        return None
    try:
        return int(math.ceil(float(booking.duration_minutes)))
    except (TypeError, ValueError):
        return None


def estimated_duration_from_lead(lead: Lead | None) -> int | None:
    if lead is None:
        return None
    snapshot = getattr(lead, "estimate_snapshot", None) or {}
    time_on_site_hours = snapshot.get("time_on_site_hours")
    if time_on_site_hours is None:
        return None
    try:
        return int(round(float(time_on_site_hours) * 60))
    except (TypeError, ValueError):
        return None
