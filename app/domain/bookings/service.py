import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import logging
from zoneinfo import ZoneInfo

from sqlalchemy import Select, and_, delete, or_, select
from sqlalchemy.exc import IntegrityError
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
from app.domain.leads.service import grant_referral_credit
from app.settings import settings

logger = logging.getLogger(__name__)

WORK_START_HOUR = 9
WORK_END_HOUR = 18
SLOT_STEP_MINUTES = 30
BUFFER_MINUTES = 30
BLOCKING_STATUSES = {"PENDING", "CONFIRMED"}
LOCAL_TZ = ZoneInfo("America/Edmonton")
DEFAULT_TEAM_NAME = "Default Team"
MIN_SLOTS_SUGGESTED = 2
MAX_SLOTS_SUGGESTED = 3


@dataclass(frozen=True)
class DurationRule:
    min_minutes: int
    max_minutes: int
BOOKING_TRANSITIONS = {
    "PENDING": {"CONFIRMED", "CANCELLED"},
    "CONFIRMED": {"DONE", "CANCELLED"},
    "DONE": set(),
    "CANCELLED": set(),
}


@dataclass
class DepositDecision:
    required: bool
    reasons: list[str]
    deposit_cents: int | None = None


@dataclass
class TimeWindowPreference:
    start_hour: int
    end_hour: int

    def bounds(self, target_date: date) -> tuple[datetime, datetime]:
        start_local = datetime.combine(target_date, time(hour=self.start_hour, tzinfo=LOCAL_TZ))
        if self.end_hour == 24:
            end_local = datetime.combine(
                target_date + timedelta(days=1), time(hour=0, tzinfo=LOCAL_TZ)
            )
        else:
            end_local = datetime.combine(target_date, time(hour=self.end_hour, tzinfo=LOCAL_TZ))
        return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


@dataclass
class SlotSuggestionRequest:
    date: date
    duration_minutes: int
    time_window: TimeWindowPreference | None = None
    service_type: str | None = None


@dataclass
class SlotSuggestionResult:
    slots: list[datetime]
    clarifier: str | None = None


def round_duration_minutes(time_on_site_hours: float) -> int:
    minutes = max(time_on_site_hours, 0) * 60
    rounded_steps = math.ceil(minutes / SLOT_STEP_MINUTES)
    return max(rounded_steps * SLOT_STEP_MINUTES, SLOT_STEP_MINUTES)


SERVICE_DURATION_RULES: dict[str, DurationRule] = {
    CleaningType.standard.value: DurationRule(min_minutes=60, max_minutes=240),
    CleaningType.deep.value: DurationRule(min_minutes=90, max_minutes=360),
    CleaningType.move_out_empty.value: DurationRule(min_minutes=150, max_minutes=420),
    CleaningType.move_in_empty.value: DurationRule(min_minutes=150, max_minutes=420),
}
DEFAULT_DURATION_RULE = DurationRule(
    min_minutes=SLOT_STEP_MINUTES,
    max_minutes=(WORK_END_HOUR - WORK_START_HOUR) * 60,
)


def apply_duration_constraints(duration_minutes: int, service_type: str | CleaningType | None = None) -> int:
    key = None
    if isinstance(service_type, CleaningType):
        key = service_type.value
    elif isinstance(service_type, str):
        key = service_type

    rule = SERVICE_DURATION_RULES.get(key, DEFAULT_DURATION_RULE)
    bounded = max(duration_minutes, rule.min_minutes, SLOT_STEP_MINUTES)
    bounded = min(bounded, rule.max_minutes, DEFAULT_DURATION_RULE.max_minutes)
    return bounded


def _normalize_datetime(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def assert_valid_booking_transition(current: str, target: str) -> None:
    if current == target:
        return
    allowed = BOOKING_TRANSITIONS.get(current, set())
    if not allowed:
        raise ValueError(f"Booking is already in terminal status: {current}")
    if target not in allowed:
        raise ValueError(f"Cannot transition booking from {current} to {target}")


def _day_window(target_date: date) -> tuple[datetime, datetime]:
    local_start = datetime.combine(target_date, time(hour=WORK_START_HOUR, tzinfo=LOCAL_TZ))
    local_end = datetime.combine(target_date, time(hour=WORK_END_HOUR, tzinfo=LOCAL_TZ))
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def _booking_window_filters(day_start: datetime, day_end: datetime, team_id: int) -> Select:
    buffer_delta = timedelta(minutes=BUFFER_MINUTES)
    return select(Booking).where(
        and_(
            Booking.team_id == team_id,
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

    if normalized.astimezone(LOCAL_TZ).weekday() >= 5:
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


async def ensure_default_team(session: AsyncSession, lock: bool = False) -> Team:
    stmt = select(Team).where(Team.name == DEFAULT_TEAM_NAME).order_by(Team.team_id).limit(1)
    if lock:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    team = result.scalar_one_or_none()
    if team:
        return team
    team = Team(name=DEFAULT_TEAM_NAME)
    session.add(team)

    nested_transaction = await session.begin_nested() if session.in_transaction() else None

    try:
        await session.flush()
    except IntegrityError:
        if nested_transaction is not None:
            await nested_transaction.rollback()
        else:
            await session.rollback()

        result = await session.execute(stmt)
        team = result.scalar_one()
        return team
    else:
        if nested_transaction is not None:
            await nested_transaction.commit()

        await session.refresh(team)
        return team


async def generate_slots(
    target_date: date,
    duration_minutes: int,
    session: AsyncSession,
    team_id: int | None = None,
) -> list[datetime]:
    team = team_id or (await ensure_default_team(session)).team_id
    day_start, day_end = _day_window(target_date)
    duration_delta = timedelta(minutes=duration_minutes)
    buffer_delta = timedelta(minutes=BUFFER_MINUTES)

    bookings_result = await session.execute(_booking_window_filters(day_start, day_end, team))
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


class SlotProvider:
    async def suggest_slots(
        self,
        request: SlotSuggestionRequest,
        session: AsyncSession,
        team_id: int | None = None,
    ) -> SlotSuggestionResult:
        raise NotImplementedError


class StubSlotProvider(SlotProvider):
    def __init__(self, max_suggestions: int = MAX_SLOTS_SUGGESTED, min_suggestions: int = MIN_SLOTS_SUGGESTED):
        self.max_suggestions = max_suggestions
        self.min_suggestions = min_suggestions

    async def suggest_slots(
        self,
        request: SlotSuggestionRequest,
        session: AsyncSession,
        team_id: int | None = None,
    ) -> SlotSuggestionResult:
        slots = await generate_slots(request.date, request.duration_minutes, session, team_id=team_id)
        slots = sorted(slots)

        selected = self._filter_by_window(
            slots, request.time_window, request.date, request.duration_minutes
        )
        clarifier: str | None = None
        if request.time_window and len(selected) < self.min_suggestions:
            clarifier = "Limited availability in that window; can we look at nearby times the same day?"
            fallback = [slot for slot in slots if slot not in selected]
            selected = (selected + fallback)[: self.max_suggestions]
        else:
            selected = selected[: self.max_suggestions]

        if not selected:
            clarifier = clarifier or "No open slots on that day. Would you like another date?"

        return SlotSuggestionResult(slots=selected, clarifier=clarifier)

    def _filter_by_window(
        self,
        slots: list[datetime],
        time_window: TimeWindowPreference | None,
        target_date: date,
        duration_minutes: int,
    ) -> list[datetime]:
        if not time_window:
            return slots
        start, end = time_window.bounds(
            slots[0].astimezone(LOCAL_TZ).date() if slots else target_date
        )
        duration_delta = timedelta(minutes=duration_minutes)
        filtered: list[datetime] = []
        for slot in slots:
            slot_end = slot + duration_delta
            if start <= slot and slot_end <= end:
                filtered.append(slot)
        return filtered


def resolve_slot_provider() -> SlotProvider:
    mode = (getattr(settings, "slot_provider_mode", "stub") or "stub").lower()
    if mode == "stub":
        return StubSlotProvider()
    logger.warning("Unknown slot provider mode %s; using stub", mode)
    return StubSlotProvider()


async def suggest_slots(
    target_date: date,
    duration_minutes: int,
    session: AsyncSession,
    *,
    time_window: TimeWindowPreference | None = None,
    service_type: str | None = None,
    team_id: int | None = None,
    provider: SlotProvider | None = None,
) -> SlotSuggestionResult:
    active_provider = provider or resolve_slot_provider()
    request = SlotSuggestionRequest(
        date=target_date,
        duration_minutes=duration_minutes,
        time_window=time_window,
        service_type=service_type,
    )
    return await active_provider.suggest_slots(request, session, team_id=team_id)


async def is_slot_available(
    starts_at: datetime,
    duration_minutes: int,
    session: AsyncSession,
    team_id: int | None = None,
) -> bool:
    normalized = _normalize_datetime(starts_at)
    local_date = normalized.astimezone(LOCAL_TZ).date()
    slots = await generate_slots(local_date, duration_minutes, session, team_id=team_id)
    return normalized in slots


async def create_booking(
    starts_at: datetime,
    duration_minutes: int,
    lead_id: str | None,
    session: AsyncSession,
    deposit_decision: DepositDecision | None = None,
    manage_transaction: bool = True,
) -> Booking:
    normalized = _normalize_datetime(starts_at)
    decision = deposit_decision or DepositDecision(required=False, reasons=[], deposit_cents=None)

    async def _create(team: Team) -> Booking:
        if not await is_slot_available(normalized, duration_minutes, session, team_id=team.team_id):
            raise ValueError("Requested slot is no longer available")

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
        return booking

    if manage_transaction:
        transaction_ctx = session.begin_nested() if session.in_transaction() else session.begin()
        async with transaction_ctx:
            team = await ensure_default_team(session, lock=True)
            return await _create(team)

    team = await ensure_default_team(session, lock=True)
    return await _create(team)


async def reschedule_booking(
    session: AsyncSession,
    booking: Booking,
    starts_at: datetime,
    duration_minutes: int,
) -> Booking:
    normalized = _normalize_datetime(starts_at)
    team_stmt = select(Team).where(Team.team_id == booking.team_id).with_for_update()
    team_result = await session.execute(team_stmt)
    team = team_result.scalar_one()

    if not await is_slot_available(normalized, duration_minutes, session, team_id=team.team_id):
        raise ValueError("Requested slot is no longer available")

    booking.starts_at = normalized
    booking.duration_minutes = duration_minutes
    await session.commit()
    await session.refresh(booking)
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
        try:
            await log_event(
                session,
                event_type=EventType.booking_confirmed,
                booking=booking,
                lead=lead,
                estimated_revenue_cents=estimated_revenue_from_lead(lead),
                estimated_duration_minutes=estimated_duration_from_booking(booking),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "analytics_log_failed",
                extra={
                    "extra": {
                        "event_type": "booking_confirmed",
                        "booking_id": booking.booking_id,
                        "lead_id": booking.lead_id,
                        "reason": type(exc).__name__,
                    }
                },
            )
    if lead:
        try:
            await grant_referral_credit(session, lead)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "referral_credit_failed",
                extra={
                    "extra": {
                        "booking_id": booking.booking_id,
                        "lead_id": lead.lead_id,
                        "reason": type(exc).__name__,
                    }
                },
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

    if booking.deposit_status == "paid":
        return booking

    booking.deposit_status = failure_status
    if booking.status == "PENDING":
        booking.status = "CANCELLED"
    await session.commit()
    await session.refresh(booking)
    return booking


async def cleanup_stale_bookings(session: AsyncSession, older_than: timedelta) -> int:
    threshold = datetime.now(tz=timezone.utc) - older_than
    deletion = (
        delete(Booking)
        .where(and_(Booking.status == "PENDING", Booking.created_at < threshold))
        .returning(Booking.booking_id)
    )
    result = await session.execute(deletion)
    deleted = len(result.scalars().all())
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
    try:
        await log_event(
            session,
            event_type=EventType.job_completed,
            booking=booking,
            lead=lead,
            estimated_revenue_cents=estimated_revenue_from_lead(lead),
            estimated_duration_minutes=estimated_duration_from_booking(booking),
            actual_duration_minutes=actual_duration_minutes,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "analytics_log_failed",
            extra={
                "extra": {
                    "event_type": "job_completed",
                    "booking_id": booking.booking_id,
                    "lead_id": booking.lead_id,
                    "reason": type(exc).__name__,
                }
            },
        )
    await session.commit()
    await session.refresh(booking)
    return booking
