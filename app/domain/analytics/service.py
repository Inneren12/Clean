import math
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from enum import StrEnum

import sqlalchemy as sa
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
    job_time_started = "job_time_started"
    job_time_paused = "job_time_paused"
    job_time_resumed = "job_time_resumed"
    job_time_finished = "job_time_finished"


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
    session: AsyncSession, start: datetime, end: datetime, *, org_id: uuid.UUID
) -> dict[EventType, int]:
    stmt: Select = (
        select(EventLog.event_type, func.count())
        .outerjoin(Booking, Booking.booking_id == EventLog.booking_id)
        .outerjoin(Lead, Lead.lead_id == EventLog.lead_id)
        .where(
            EventLog.occurred_at >= start,
            EventLog.occurred_at <= end,
            sa.or_(Lead.org_id == org_id, Booking.org_id == org_id),
        )
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
    session: AsyncSession, start: datetime, end: datetime, *, org_id: uuid.UUID
) -> float | None:
    stmt = (
        select(func.avg(EventLog.estimated_revenue_cents))
        .outerjoin(Booking, Booking.booking_id == EventLog.booking_id)
        .outerjoin(Lead, Lead.lead_id == EventLog.lead_id)
        .where(
            EventLog.event_type.in_(
                [EventType.booking_confirmed.value, EventType.job_completed.value]
            ),
            EventLog.occurred_at >= start,
            EventLog.occurred_at <= end,
            EventLog.estimated_revenue_cents.isnot(None),
            sa.or_(Lead.org_id == org_id, Booking.org_id == org_id),
        )
    )
    result = await session.execute(stmt)
    avg_value = result.scalar_one_or_none()
    return float(avg_value) if avg_value is not None else None


async def duration_accuracy(
    session: AsyncSession, start: datetime, end: datetime, *, org_id: uuid.UUID
) -> tuple[float | None, float | None, float | None, int]:
    stmt = (
        select(Booking.duration_minutes, Booking.actual_duration_minutes)
        .join(EventLog, EventLog.booking_id == Booking.booking_id)
        .where(
            EventLog.event_type == EventType.job_completed.value,
            EventLog.occurred_at >= start,
            EventLog.occurred_at <= end,
            Booking.actual_duration_minutes.isnot(None),
            Booking.org_id == org_id,
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


def _labor_cost_cents_expression(bind, lead_table: Lead) -> sa.ColumnElement:
    if bind and bind.dialect.name == "postgresql":
        labor_text = lead_table.estimate_snapshot["labor_cost"].astext
        is_numeric = labor_text.op("~")(r"^-?\d+(?:\.\d+)?$")
        labor_value = sa.case(
            (is_numeric, sa.cast(labor_text, sa.Numeric())),
            else_=0,
        )
    else:
        labor_text = func.json_extract(lead_table.estimate_snapshot, "$.labor_cost")
        json_type = func.json_type(lead_table.estimate_snapshot, "$.labor_cost")
        labor_value = sa.case(
            (json_type.in_(["integer", "real"]), sa.cast(labor_text, sa.Float)),
            else_=0,
        )
    return func.coalesce(labor_value * 100, 0)


def _day_diff_expression(bind, current: sa.ColumnElement, previous: sa.ColumnElement) -> sa.ColumnElement:
    if bind and bind.dialect.name == "postgresql":
        return func.extract("epoch", current - previous) / 86400.0
    return func.julianday(current) - func.julianday(previous)


async def kpi_aggregates(
    session: AsyncSession, start: datetime, end: datetime, *, org_id: uuid.UUID
) -> dict[str, object]:
    bind = session.get_bind()
    revenue_expr = Booking.base_charge_cents - Booking.refund_total_cents - Booking.credit_note_total_cents
    planned_minutes = func.coalesce(Booking.planned_minutes, Booking.duration_minutes)
    planned_seconds = planned_minutes * 60
    actual_seconds = func.coalesce(Booking.actual_seconds, Booking.actual_duration_minutes * 60, 0)
    labor_cents = _labor_cost_cents_expression(bind, Lead)

    aggregate_stmt = (
        select(
            func.count().label("total_bookings"),
            func.count().filter(Booking.status == "DONE").label("completed_bookings"),
            func.count().filter(Booking.status == "CANCELLED").label("cancelled_bookings"),
            func.sum(func.coalesce(revenue_expr, 0)).filter(Booking.status == "DONE").label("total_revenue_cents"),
            func.sum(func.coalesce(planned_seconds, 0)).filter(Booking.status == "DONE").label("planned_seconds"),
            func.sum(func.coalesce(actual_seconds, 0)).filter(Booking.status == "DONE").label("actual_seconds"),
            func.sum(func.coalesce(labor_cents, 0)).filter(Booking.status == "DONE").label("labor_cents"),
        )
        .select_from(Booking)
        .join(Lead, Booking.lead_id == Lead.lead_id, isouter=True)
        .where(
            Booking.starts_at >= start,
            Booking.starts_at <= end,
            Booking.org_id == org_id,
            sa.or_(Lead.org_id == org_id, Lead.lead_id.is_(None)),
        )
    )

    aggregate_row = await session.execute(aggregate_stmt)
    (
        total_bookings,
        completed_bookings,
        cancelled_bookings,
        total_revenue_cents,
        planned_seconds_total,
        actual_seconds_total,
        labor_cents_total,
    ) = aggregate_row.one()

    total_bookings = int(total_bookings or 0)
    completed_bookings = int(completed_bookings or 0)
    cancelled_bookings = int(cancelled_bookings or 0)
    total_revenue_cents = int(total_revenue_cents or 0)
    planned_seconds_total = float(planned_seconds_total or 0)
    actual_seconds_total = float(actual_seconds_total or 0)
    labor_cents_total = int(round(float(labor_cents_total or 0)))

    window = end - start
    day_span = max(1, math.ceil(window.total_seconds() / 86400))
    revenue_per_day = float(round(total_revenue_cents / day_span, 2))
    average_order_value = (
        float(round(total_revenue_cents / completed_bookings, 2))
        if completed_bookings > 0
        else None
    )
    margin_cents = total_revenue_cents - labor_cents_total
    crew_utilization = (
        round(actual_seconds_total / planned_seconds_total, 4)
        if planned_seconds_total > 0
        else None
    )
    cancellation_rate = (
        round(cancelled_bookings / total_bookings, 4) if total_bookings > 0 else 0.0
    )

    completed_subquery = (
        select(
            func.coalesce(Booking.client_id, Booking.lead_id).label("customer_id"),
            Booking.starts_at.label("starts_at"),
        )
        .where(Booking.status == "DONE", Booking.org_id == org_id)
        .subquery()
    )

    lagged = select(
        completed_subquery.c.customer_id,
        completed_subquery.c.starts_at.label("current_start"),
        func.lag(completed_subquery.c.starts_at)
        .over(
            partition_by=completed_subquery.c.customer_id,
            order_by=completed_subquery.c.starts_at,
        )
        .label("prev_start"),
    ).subquery()

    range_rows = select(lagged).where(
        lagged.c.current_start >= start,
        lagged.c.current_start <= end,
    )

    range_subquery = range_rows.subquery()
    day_diff = _day_diff_expression(bind, range_subquery.c.current_start, range_subquery.c.prev_start)

    base_customers_stmt = select(
        func.count(sa.distinct(completed_subquery.c.customer_id))
    ).where(completed_subquery.c.starts_at >= start, completed_subquery.c.starts_at <= end)

    retention_stmt = select(
        func.count(sa.distinct(range_subquery.c.customer_id)).filter(
            range_subquery.c.prev_start.isnot(None)
        ).label("customers_with_history"),
        func.count(sa.distinct(range_subquery.c.customer_id)).filter(
            sa.and_(range_subquery.c.prev_start.isnot(None), day_diff <= 30)
        ).label("retained_30"),
        func.count(sa.distinct(range_subquery.c.customer_id)).filter(
            sa.and_(range_subquery.c.prev_start.isnot(None), day_diff <= 60)
        ).label("retained_60"),
        func.count(sa.distinct(range_subquery.c.customer_id)).filter(
            sa.and_(range_subquery.c.prev_start.isnot(None), day_diff <= 90)
        ).label("retained_90"),
    )

    customers_result = await session.execute(base_customers_stmt)
    customers_in_range = int(customers_result.scalar_one() or 0)

    retention_result = await session.execute(retention_stmt)
    customers_with_history, retained_30, retained_60, retained_90 = retention_result.one()

    def _rate(value: int) -> float:
        if customers_in_range == 0:
            return 0.0
        return round((value or 0) / customers_in_range, 4)

    return {
        "financial": {
            "total_revenue_cents": total_revenue_cents,
            "revenue_per_day_cents": revenue_per_day,
            "margin_cents": margin_cents,
            "average_order_value_cents": average_order_value,
        },
        "operational": {
            "crew_utilization": crew_utilization,
            "cancellation_rate": cancellation_rate,
            "retention_30_day": _rate(int(retained_30 or 0)),
            "retention_60_day": _rate(int(retained_60 or 0)),
            "retention_90_day": _rate(int(retained_90 or 0)),
        },
    }
