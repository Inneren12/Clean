from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.bookings import service as booking_service
from app.domain.bookings.db_models import Booking
from app.domain.clients.db_models import ClientUser
from app.domain.invoices.schemas import InvoiceItemCreate
from app.domain.invoices.service import create_invoice_from_order
from app.domain.subscriptions import statuses
from app.domain.subscriptions.db_models import Subscription
from app.domain.subscriptions.schemas import SubscriptionCreateRequest
from app.infra.email import EmailAdapter


LOCAL_TZ = booking_service.LOCAL_TZ


def _start_of_day_utc(target_date: date) -> datetime:
    local_dt = datetime.combine(
        target_date, time(hour=booking_service.WORK_START_HOUR, tzinfo=LOCAL_TZ)
    )
    return local_dt.astimezone(timezone.utc)


def _resolve_first_run_date(payload: SubscriptionCreateRequest) -> date:
    if payload.frequency == statuses.MONTHLY:
        preferred_day = payload.preferred_day_of_month or payload.start_date.day
        if payload.start_date.day <= preferred_day:
            last_day = calendar.monthrange(payload.start_date.year, payload.start_date.month)[1]
            clamped_day = min(preferred_day, last_day)
            return payload.start_date.replace(day=clamped_day)
        month = payload.start_date.month + 1
        year = payload.start_date.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        last_day = calendar.monthrange(year, month)[1]
        clamped_day = min(preferred_day, last_day)
        return date(year=year, month=month, day=clamped_day)

    target_weekday = payload.preferred_weekday
    if target_weekday is None:
        target_weekday = payload.start_date.weekday()

    days_ahead = (target_weekday - payload.start_date.weekday()) % 7
    return payload.start_date + timedelta(days=days_ahead)


def _next_date(subscription: Subscription, current_date: date) -> date:
    if subscription.frequency == statuses.WEEKLY:
        return current_date + timedelta(days=7)
    if subscription.frequency == statuses.BIWEEKLY:
        return current_date + timedelta(days=14)
    if subscription.frequency == statuses.MONTHLY:
        month = current_date.month + 1
        year = current_date.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        day = subscription.preferred_day_of_month or current_date.day
        last_day = calendar.monthrange(year, month)[1]
        clamped_day = min(day, last_day)
        return date(year=year, month=month, day=clamped_day)
    raise ValueError("Unknown frequency")


async def create_subscription(
    session: AsyncSession, client_id: str, payload: SubscriptionCreateRequest
) -> Subscription:
    first_date = _resolve_first_run_date(payload)
    # For monthly subscriptions, ensure preferred_day_of_month is set
    preferred_day = payload.preferred_day_of_month
    if payload.frequency == statuses.MONTHLY and preferred_day is None:
        preferred_day = payload.start_date.day
    subscription = Subscription(
        client_id=client_id,
        status=statuses.ACTIVE,
        frequency=payload.frequency,
        start_date=payload.start_date,
        next_run_at=_start_of_day_utc(first_date),
        preferred_weekday=payload.preferred_weekday,
        preferred_day_of_month=preferred_day,
        base_service_type=payload.base_service_type,
        base_price=payload.base_price,
    )
    session.add(subscription)
    await session.flush()
    await session.refresh(subscription)
    return subscription


async def list_client_subscriptions(session: AsyncSession, client_id: str) -> list[Subscription]:
    stmt = (
        select(Subscription)
        .where(Subscription.client_id == client_id)
        .order_by(Subscription.created_at.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def update_subscription_status(
    session: AsyncSession, subscription: Subscription, new_status: str
) -> Subscription:
    subscription.status = statuses.normalize_status(new_status)
    await session.flush()
    await session.refresh(subscription)
    return subscription


@dataclass
class GenerationResult:
    processed: int
    created_orders: int


async def generate_due_orders(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    email_adapter: EmailAdapter | None = None,
) -> GenerationResult:
    current = now or datetime.now(timezone.utc)
    stmt = (
        select(Subscription)
        .where(Subscription.status == statuses.ACTIVE)
        .where(Subscription.next_run_at <= current)
    )
    result = await session.execute(stmt)
    subscriptions = result.scalars().all()
    created = 0

    for subscription in subscriptions:
        scheduled_date = subscription.next_run_at.date()
        existing = await session.scalar(
            select(Booking).where(
                and_(
                    Booking.subscription_id == subscription.subscription_id,
                    Booking.scheduled_date == scheduled_date,
                )
            )
        )
        if existing:
            subscription.next_run_at = _start_of_day_utc(_next_date(subscription, scheduled_date))
            continue

        starts_at = _start_of_day_utc(scheduled_date)

        booking = await booking_service.create_booking(
            starts_at=starts_at,
            duration_minutes=120,
            lead_id=None,
            session=session,
            manage_transaction=False,
            client_id=subscription.client_id,
            subscription_id=subscription.subscription_id,
            scheduled_date=scheduled_date,
        )

        invoice_item = InvoiceItemCreate(
            description=f"{subscription.base_service_type} cleaning",
            qty=1,
            unit_price_cents=subscription.base_price,
        )
        await create_invoice_from_order(session, booking, [invoice_item])

        await _notify_client(session, email_adapter, subscription, booking)
        subscription.next_run_at = _start_of_day_utc(_next_date(subscription, scheduled_date))
        created += 1

    return GenerationResult(processed=len(subscriptions), created_orders=created)


async def _notify_client(
    session: AsyncSession,
    email_adapter: EmailAdapter | None,
    subscription: Subscription,
    booking: Booking,
) -> None:
    if email_adapter is None:
        return
    client = await session.get(ClientUser, subscription.client_id)
    if not client or not client.email:
        return
    subject = "Your recurring cleaning has been scheduled"
    body = (
        f"Hi {client.name or 'there'},\n\n"
        "We've generated your next cleaning order from your subscription.\n"
        f"Appointment: {booking.starts_at.astimezone(LOCAL_TZ).strftime('%Y-%m-%d %H:%M %Z')}\n\n"
        "Reply to this email if you need to adjust anything."
    )
    try:
        await email_adapter.send_email(recipient=client.email, subject=subject, body=body)
    except Exception:
        pass
