import logging
from datetime import datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.bookings.db_models import Booking, EmailEvent
from app.domain.leads.db_models import Lead
from app.infra.email import EmailAdapter

LOCAL_TZ = ZoneInfo("America/Edmonton")
logger = logging.getLogger(__name__)

EMAIL_TYPE_BOOKING_PENDING = "booking_pending"
EMAIL_TYPE_BOOKING_CONFIRMED = "booking_confirmed"
EMAIL_TYPE_BOOKING_REMINDER = "booking_reminder_24h"
EMAIL_TYPE_BOOKING_COMPLETED = "booking_completed"
EMAIL_TYPE_NPS_SURVEY = "nps_survey"
REMINDER_STATUSES = {"CONFIRMED", "PENDING"}


def _format_start_time(booking: Booking) -> str:
    starts_at = booking.starts_at
    if starts_at.tzinfo is None:
        starts_at = starts_at.replace(tzinfo=timezone.utc)
    return starts_at.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M %Z")


def _render_booking_pending(booking: Booking, lead: Lead) -> tuple[str, str]:
    subject = "Booking request received"
    body = (
        f"Hi {lead.name},\n\n"
        "We've saved your cleaning booking request. Our team will review details and confirm soon.\n\n"
        f"Requested time: {_format_start_time(booking)}\n"
        "If anything changes, just reply to this email."
    )
    return subject, body


def _render_booking_confirmed(booking: Booking, lead: Lead) -> tuple[str, str]:
    subject = "Cleaning booking confirmed"
    body = (
        f"Hi {lead.name},\n\n"
        "Your cleaning booking is confirmed. We'll see you soon!\n\n"
        f"Appointment time: {_format_start_time(booking)}\n"
        "Reply to this email if you have updates."
    )
    return subject, body


def _render_booking_reminder(booking: Booking, lead: Lead) -> tuple[str, str]:
    subject = "Reminder: cleaning in the next 24 hours"
    body = (
        f"Hi {lead.name},\n\n"
        "Friendly reminder that your cleaning is coming up within the next day.\n\n"
        f"Appointment time: {_format_start_time(booking)}\n"
        "If you need to adjust anything, reply to this email and we'll help."
    )
    return subject, body


def _render_booking_completed(booking: Booking, lead: Lead) -> tuple[str, str]:
    subject = "Thanks for choosing us — quick review?"
    body = (
        f"Hi {lead.name},\n\n"
        "Thanks for letting us clean your place. If you have a moment, we'd love a quick review.\n\n"
        "Review link: https://example.com/review-placeholder\n\n"
        "If anything was missed, reply so we can make it right."
    )
    return subject, body


def _render_nps_survey(lead: Lead, survey_link: str) -> tuple[str, str]:
    subject = "How did we do? Quick 1-question check-in"
    body = (
        f"Hi {lead.name},\n\n"
        "Thanks again for choosing us. Could you rate your last cleaning?"
        " It only takes a few seconds.\n\n"
        f"Share your score: {survey_link}\n\n"
        "If anything was off, reply and we'll make it right."
    )
    return subject, body


async def _try_send_email(
    adapter: EmailAdapter | None,
    recipient: str,
    subject: str,
    body: str,
    *,
    context: dict | None = None,
) -> bool:
    if adapter is None:
        logger.warning("email_adapter_missing", extra={"extra": context or {}})
        return False
    try:
        delivered = await adapter.send_email(recipient=recipient, subject=subject, body=body)
        return bool(delivered)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "email_send_failed",
            extra={"extra": {**(context or {}), "reason": type(exc).__name__}},
        )
        return False


async def _already_sent(session: AsyncSession, booking_id: str, email_type: str) -> bool:
    stmt = select(EmailEvent.event_id).where(
        and_(EmailEvent.booking_id == booking_id, EmailEvent.email_type == email_type)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def _send_with_record(
    session: AsyncSession,
    adapter: EmailAdapter | None,
    booking: Booking,
    lead: Lead,
    email_type: str,
    render: Callable[[Booking, Lead], tuple[str, str]],
    dedupe: bool = True,
) -> bool:
    if not lead.email:
        return False
    if dedupe and await _already_sent(session, booking.booking_id, email_type):
        return False

    subject, body = render(booking, lead)
    delivered = await _try_send_email(
        adapter,
        lead.email,
        subject,
        body,
        context={"booking_id": booking.booking_id, "email_type": email_type},
    )
    if not delivered:
        return False

    event = EmailEvent(
        booking_id=booking.booking_id,
        email_type=email_type,
        recipient=lead.email,
        subject=subject,
        body=body,
    )
    session.add(event)
    await session.commit()
    return True


async def send_booking_pending_email(
    session: AsyncSession, adapter: EmailAdapter | None, booking: Booking, lead: Lead
) -> bool:
    return await _send_with_record(
        session=session,
        adapter=adapter,
        booking=booking,
        lead=lead,
        email_type=EMAIL_TYPE_BOOKING_PENDING,
        render=_render_booking_pending,
    )


async def send_booking_confirmed_email(
    session: AsyncSession, adapter: EmailAdapter | None, booking: Booking, lead: Lead
) -> bool:
    return await _send_with_record(
        session=session,
        adapter=adapter,
        booking=booking,
        lead=lead,
        email_type=EMAIL_TYPE_BOOKING_CONFIRMED,
        render=_render_booking_confirmed,
    )


async def send_booking_reminder_email(
    session: AsyncSession, adapter: EmailAdapter | None, booking: Booking, lead: Lead, dedupe: bool = True
) -> bool:
    return await _send_with_record(
        session=session,
        adapter=adapter,
        booking=booking,
        lead=lead,
        email_type=EMAIL_TYPE_BOOKING_REMINDER,
        render=_render_booking_reminder,
        dedupe=dedupe,
    )


async def send_booking_completed_email(
    session: AsyncSession, adapter: EmailAdapter | None, booking: Booking, lead: Lead, dedupe: bool = True
) -> bool:
    return await _send_with_record(
        session=session,
        adapter=adapter,
        booking=booking,
        lead=lead,
        email_type=EMAIL_TYPE_BOOKING_COMPLETED,
        render=_render_booking_completed,
        dedupe=dedupe,
    )


async def send_nps_survey_email(
    session: AsyncSession,
    adapter: EmailAdapter | None,
    booking: Booking,
    lead: Lead,
    survey_link: str,
    *,
    dedupe: bool = True,
) -> bool:
    return await _send_with_record(
        session=session,
        adapter=adapter,
        booking=booking,
        lead=lead,
        email_type=EMAIL_TYPE_NPS_SURVEY,
        render=lambda _booking, _lead: _render_nps_survey(_lead, survey_link),
        dedupe=dedupe,
    )


async def scan_and_send_reminders(session: AsyncSession, adapter: EmailAdapter | None) -> dict[str, int]:
    now = datetime.now(tz=timezone.utc)
    window_end = now + timedelta(hours=24)
    stmt = (
        select(Booking, Lead)
        .join(Lead, Lead.lead_id == Booking.lead_id)
        .where(
            Booking.starts_at >= now,
            Booking.starts_at <= window_end,
            Booking.status.in_(REMINDER_STATUSES),
            Lead.email.isnot(None),
        )
    )
    result = await session.execute(stmt)
    sent = 0
    skipped = 0
    for booking, lead in result.all():
        delivered = await send_booking_reminder_email(session, adapter, booking, lead, dedupe=True)
        if delivered:
            sent += 1
        else:
            skipped += 1
    return {"sent": sent, "skipped": skipped}


async def resend_last_email(
    session: AsyncSession, adapter: EmailAdapter | None, booking_id: str
) -> dict[str, str]:
    stmt = (
        select(EmailEvent)
        .where(EmailEvent.booking_id == booking_id)
        .order_by(EmailEvent.created_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    event = result.scalar_one_or_none()
    if event is None:
        raise LookupError("no_email_event")

    delivered = await _try_send_email(
        adapter,
        event.recipient,
        event.subject,
        event.body,
        context={"booking_id": booking_id, "email_type": event.email_type},
    )
    if not delivered:
        raise RuntimeError("email_send_failed")

    replay = EmailEvent(
        booking_id=booking_id,
        email_type=event.email_type,
        recipient=event.recipient,
        subject=event.subject,
        body=event.body,
    )
    session.add(replay)
    await session.commit()
    return {"booking_id": booking_id, "email_type": event.email_type, "recipient": event.recipient}
