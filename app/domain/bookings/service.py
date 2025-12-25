import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.analytics.service import (
    EventType,
    estimated_duration_from_booking,
    estimated_revenue_from_lead,
    log_event,
)
from app.domain.bookings.db_models import Booking, Team
from app.domain.notifications import email_service
from app.domain.pricing.models import CleaningType
from app.domain.leads.db_models import Lead

WORK_START_HOUR = 9
WORK_END_HOUR = 18
SLOT_STEP_MINUTES = 30
BUFFER_MINUTES = 30
BLOCKING_STATUSES = {"PENDING", "CONFIRMED"}


@dataclass
class DepositDecision:
    required: bool
    reasons: list[str]
    deposit_cents: int | None = None


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


async def _has_existing_history(session: AsyncSession, lead_id: str) -> bool:
    stmt = select(Booking.booking_id).where(
        Booking.lead_id == lead_id, Booking.status.in_({"CONFIRMED", "DONE"})
    )
    result = await session.execute(stmt.limit(1))
    return result.scalar_one_or_none() is not None


async def evaluate_deposit_policy(
    session: AsyncSession, lead: Lead | None, starts_at: datetime, deposit_percent: float
) -> DepositDecision:
    normalized = _normalize_datetime(starts_at)
    reasons: list[str] = []

    if normalized.weekday() >= 5:
        reasons.append("weekend")

    estimated_total = None
    if lead:
        cleaning_type = (lead.structured_inputs or {}).get("cleaning_type")
        if cleaning_type in {CleaningType.deep.value, CleaningType.move_out_empty.value}:
            reasons.append("heavy_cleaning")

        if not await _has_existing_history(session, lead.lead_id):
            reasons.append("new_client")

        estimated_total = (lead.estimate_snapshot or {}).get("total_before_tax")

    required = bool(reasons)
    deposit_cents = None
    if required and estimated_total is not None:
        deposit_cents = max(0, math.ceil(float(estimated_total) * deposit_percent * 100))

    return DepositDecision(required=required, reasons=reasons, deposit_cents=deposit_cents)


async def ensure_default_team(session: AsyncSession) -> Team:
    result = await session.execute(select(Team).order_by(Team.team_id).limit(1))
    team = result.scalar_one_or_none()
    if team:
        return team
    team = Team(name="Default Team")
    session.add(team)
    await session.flush()
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
    deposit_decision: DepositDecision | None = None,
    commit: bool = True,
) -> Booking:
    normalized = _normalize_datetime(starts_at)
    if not await is_slot_available(normalized, duration_minutes, session):
        raise ValueError("Requested slot is no longer available")

    decision = deposit_decision or DepositDecision(required=False, reasons=[], deposit_cents=None)
    team = await ensure_default_team(session)
    booking = Booking(
        team_id=team.team_id,
        lead_id=lead_id,
        starts_at=normalized,
        duration_minutes=duration_minutes,
        status="PENDING",
        deposit_required=decision.required,
        deposit_cents=decision.deposit_cents,
        deposit_policy=decision.reasons,
        deposit_status="pending" if decision.required else None,
    )
    session.add(booking)
    await session.flush()
    await session.refresh(booking)
    if commit:
        await session.commit()
    return booking


async def attach_checkout_session(
    session: AsyncSession,
    booking_id: str,
    checkout_session_id: str,
    payment_intent_id: str | None = None,
    commit: bool = True,
) -> Booking | None:
    booking = await session.get(Booking, booking_id)
    if booking is None:
        return None

    booking.stripe_checkout_session_id = checkout_session_id
    if payment_intent_id:
        booking.stripe_payment_intent_id = payment_intent_id
    if booking.deposit_required:
        booking.deposit_status = booking.deposit_status or "pending"
    await session.flush()
    await session.refresh(booking)
    if commit:
        await session.commit()
    return booking


async def mark_deposit_paid(
    session: AsyncSession, checkout_session_id: str | None, payment_intent_id: str | None, email_adapter
) -> Booking | None:
    conditions = []
    if checkout_session_id:
        conditions.append(Booking.stripe_checkout_session_id == checkout_session_id)
    if payment_intent_id:
        conditions.append(Booking.stripe_payment_intent_id == payment_intent_id)
    if not conditions:
        return None

    stmt = select(Booking).where(or_(*conditions)).limit(1)
    result = await session.execute(stmt)
    booking = result.scalar_one_or_none()
    if booking is None:
        return None

    already_confirmed = booking.deposit_status == "paid" and booking.status == "CONFIRMED"
    booking.deposit_status = "paid"
    booking.status = "CONFIRMED"
    if payment_intent_id:
        booking.stripe_payment_intent_id = payment_intent_id
    lead = await session.get(Lead, booking.lead_id) if booking.lead_id else None
    if not already_confirmed:
        await log_event(
            session,
            event_type=EventType.booking_confirmed,
            booking=booking,
            lead=lead,
            estimated_revenue_cents=estimated_revenue_from_lead(lead),
            estimated_duration_minutes=estimated_duration_from_booking(booking),
        )
    await session.commit()
    await session.refresh(booking)

    if booking.lead_id:
        lead = await session.get(Lead, booking.lead_id)
        if lead:
            await email_service.send_booking_confirmed_email(session, email_adapter, booking, lead)

    return booking


async def mark_deposit_failed(
    session: AsyncSession, checkout_session_id: str | None, payment_intent_id: str | None, failure_status: str = "expired"
) -> Booking | None:
    conditions = []
    if checkout_session_id:
        conditions.append(Booking.stripe_checkout_session_id == checkout_session_id)
    if payment_intent_id:
        conditions.append(Booking.stripe_payment_intent_id == payment_intent_id)
    if not conditions:
        return None

    stmt = select(Booking).where(or_(*conditions)).limit(1)
    result = await session.execute(stmt)
    booking = result.scalar_one_or_none()
    if booking is None:
        return None

    booking.deposit_status = failure_status
    if booking.status == "PENDING":
        booking.status = "CANCELLED"
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


async def mark_booking_completed(
    session: AsyncSession, booking_id: str, actual_duration_minutes: int
) -> Booking | None:
    if actual_duration_minutes <= 0:
        raise ValueError("actual_duration_minutes must be positive")

    booking = await session.get(Booking, booking_id)
    if booking is None:
        return None

    if booking.actual_duration_minutes is not None:
        raise ValueError("Booking already completed")

    booking.actual_duration_minutes = actual_duration_minutes
    booking.status = "DONE"
    lead = await session.get(Lead, booking.lead_id) if booking.lead_id else None
    await log_event(
        session,
        event_type=EventType.job_completed,
        booking=booking,
        lead=lead,
        estimated_revenue_cents=estimated_revenue_from_lead(lead),
        estimated_duration_minutes=estimated_duration_from_booking(booking),
        actual_duration_minutes=actual_duration_minutes,
    )
    await session.commit()
    await session.refresh(booking)
    return booking
