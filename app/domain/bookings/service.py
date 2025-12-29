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
from app.domain.bookings.policy import (
    BookingPolicySnapshot,
    CancellationPolicySnapshot,
    CancellationWindow,
    DepositSnapshot,
)
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
    policy_snapshot: BookingPolicySnapshot | None = None
    cancellation_policy: CancellationPolicySnapshot | None = None


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

HEAVY_SERVICES = {
    CleaningType.deep.value,
    CleaningType.move_out_empty.value,
    CleaningType.move_in_empty.value,
}
LATE_NOTICE_HOURS = 48
SHORT_NOTICE_HOURS = 24
HIGH_VALUE_THRESHOLD_CENTS = 30000
MIN_DEPOSIT_CENTS = 5000
MAX_DEPOSIT_CENTS = 20000


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


def _lead_time_hours(starts_at: datetime) -> float:
    now = datetime.now(timezone.utc)
    delta = starts_at - now
    return max(0.0, round(delta.total_seconds() / 3600, 2))


def _resolve_service_type(service_type: str | CleaningType | None, lead: Lead | None) -> str | None:
    if isinstance(service_type, CleaningType):
        return service_type.value
    if isinstance(service_type, str):
        return service_type
    if lead:
        return (lead.structured_inputs or {}).get("cleaning_type")
    return None


def _estimate_total_cents(lead: Lead | None, estimated_total: float | int | None) -> int | None:
    total = estimated_total
    if total is None and lead:
        total = (lead.estimate_snapshot or {}).get("total_before_tax")
    if total is None:
        return None
    try:
        return math.ceil(float(total) * 100)
    except Exception:  # noqa: BLE001
        return None


def _build_cancellation_policy(
    service_type: str | None,
    lead_time_hours: float,
    total_cents: int | None,
    first_time: bool,
) -> CancellationPolicySnapshot:
    heavy_service = service_type in HEAVY_SERVICES
    free_cutoff = 72 if heavy_service else 48
    partial_start = 48 if heavy_service else 24
    partial_refund = 50
    rules: list[str] = []

    if heavy_service:
        rules.append("heavy_service")
    if first_time:
        rules.append("first_time_client")
        partial_refund = min(partial_refund, 40)
    if total_cents is not None and total_cents >= HIGH_VALUE_THRESHOLD_CENTS:
        rules.append("high_value_booking")
        partial_refund = min(partial_refund, 25)
    if lead_time_hours < LATE_NOTICE_HOURS:
        rules.append("late_booking")
    if lead_time_hours < SHORT_NOTICE_HOURS:
        rules.append("short_notice")
        partial_refund = min(partial_refund, 25)

    partial_start = min(partial_start, free_cutoff)

    windows = [
        CancellationWindow(
            label="free",
            start_hours_before=float(free_cutoff),
            end_hours_before=None,
            refund_percent=100,
        ),
        CancellationWindow(
            label="partial",
            start_hours_before=float(partial_start),
            end_hours_before=float(free_cutoff),
            refund_percent=int(partial_refund),
        ),
        CancellationWindow(
            label="late",
            start_hours_before=0.0,
            end_hours_before=float(partial_start),
            refund_percent=0,
        ),
    ]

    return CancellationPolicySnapshot(rules=rules, windows=windows)


def _build_deposit_snapshot(
    reasons: list[str],
    deposit_percent: float,
    service_type: str | None,
    total_cents: int | None,
    lead_time_hours: float,
) -> DepositSnapshot:
    required = bool(reasons)
    percent = deposit_percent
    heavy_service = service_type in HEAVY_SERVICES

    if heavy_service:
        percent = max(percent, 0.35)
    if lead_time_hours < SHORT_NOTICE_HOURS:
        percent = max(percent, 0.5)
    elif lead_time_hours < LATE_NOTICE_HOURS:
        percent = max(percent, 0.4)
    if total_cents is not None and total_cents >= HIGH_VALUE_THRESHOLD_CENTS:
        percent = max(percent, 0.3)

    basis = "none"
    amount_cents: int | None = None
    if required:
        basis = "percent_clamped"
        estimated = total_cents if total_cents is not None else MIN_DEPOSIT_CENTS
        amount_cents = math.ceil(estimated * percent)
        amount_cents = max(MIN_DEPOSIT_CENTS, amount_cents)
        amount_cents = min(MAX_DEPOSIT_CENTS, amount_cents)
        if total_cents is None:
            basis = "fixed_minimum"

    return DepositSnapshot(
        required=required,
        amount_cents=amount_cents,
        percent_applied=percent if required else None,
        min_cents=MIN_DEPOSIT_CENTS,
        max_cents=MAX_DEPOSIT_CENTS,
        reasons=reasons,
        basis=basis,
    )


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
    session: AsyncSession,
    lead: Lead | None,
    starts_at: datetime,
    deposit_percent: float,
    service_type: str | CleaningType | None = None,
    estimated_total: float | int | None = None,
) -> DepositDecision:
    normalized = _normalize_datetime(starts_at)
    service_value = _resolve_service_type(service_type, lead)
    total_cents = _estimate_total_cents(lead, estimated_total)
    lead_time_hours = _lead_time_hours(normalized)
    first_time = bool(lead) and not await _has_existing_history(session, lead.lead_id)

    reasons: list[str] = []
    if first_time:
        reasons.append("first_time_client")
    if service_value in HEAVY_SERVICES:
        reasons.append(f"service_type_{service_value}")
    if lead_time_hours < SHORT_NOTICE_HOURS:
        reasons.append("short_notice")
    elif lead_time_hours < LATE_NOTICE_HOURS:
        reasons.append("late_booking")
    if total_cents is not None and total_cents >= HIGH_VALUE_THRESHOLD_CENTS:
        reasons.append("high_value_booking")

    deposit_snapshot = _build_deposit_snapshot(
        reasons=reasons,
        deposit_percent=deposit_percent,
        service_type=service_value,
        total_cents=total_cents,
        lead_time_hours=lead_time_hours,
    )
    cancellation_policy = _build_cancellation_policy(
        service_type=service_value,
        lead_time_hours=lead_time_hours,
        total_cents=total_cents,
        first_time=first_time,
    )

    policy_snapshot = BookingPolicySnapshot(
        lead_time_hours=lead_time_hours,
        service_type=service_value,
        total_amount_cents=total_cents,
        first_time_client=first_time,
        deposit=deposit_snapshot,
        cancellation=cancellation_policy,
    )

    return DepositDecision(
        required=deposit_snapshot.required,
        reasons=reasons,
        deposit_cents=deposit_snapshot.amount_cents,
        policy_snapshot=policy_snapshot,
        cancellation_policy=cancellation_policy,
    )


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
    policy_snapshot: BookingPolicySnapshot | None = None,
    manage_transaction: bool = True,
    client_id: str | None = None,
    subscription_id: str | None = None,
    scheduled_date: date | None = None,
) -> Booking:
    normalized = _normalize_datetime(starts_at)
    decision = deposit_decision or DepositDecision(required=False, reasons=[], deposit_cents=None)
    snapshot = policy_snapshot or decision.policy_snapshot

    snapshot_payload: dict | None = None
    if snapshot:
        snapshot_payload = snapshot.model_dump(mode="json") if hasattr(snapshot, "model_dump") else snapshot

    async def _create(team: Team) -> Booking:
        if not await is_slot_available(normalized, duration_minutes, session, team_id=team.team_id):
            raise ValueError("Requested slot is no longer available")

        booking = Booking(
            team_id=team.team_id,
            lead_id=lead_id,
            client_id=client_id,
            starts_at=normalized,
            duration_minutes=duration_minutes,
            planned_minutes=duration_minutes,
            status="PENDING",
            subscription_id=subscription_id,
            scheduled_date=scheduled_date,
            deposit_required=decision.required,
            deposit_cents=decision.deposit_cents,
            deposit_policy=decision.reasons,
            deposit_status="pending" if decision.required else None,
            policy_snapshot=snapshot_payload,
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
    booking.actual_seconds = actual_duration_minutes * 60
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
