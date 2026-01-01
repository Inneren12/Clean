import asyncio
import base64
import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.bookings.db_models import Booking, EmailEvent
from app.domain.invoices import service as invoice_service
from app.domain.invoices.db_models import Invoice
from app.domain.leads.db_models import Lead
from app.domain.notifications.db_models import EmailFailure, Unsubscribe
from app.infra.email import EmailAdapter
from app.settings import settings

LOCAL_TZ = ZoneInfo("America/Edmonton")
logger = logging.getLogger(__name__)


def ensure_event_loop() -> asyncio.AbstractEventLoop:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


# Ensure a loop exists for call sites that expect get_event_loop() to work.
ensure_event_loop()

EMAIL_TYPE_BOOKING_PENDING = "booking_pending"
EMAIL_TYPE_BOOKING_CONFIRMED = "booking_confirmed"
EMAIL_TYPE_BOOKING_REMINDER = "booking_reminder_24h"
EMAIL_TYPE_BOOKING_COMPLETED = "booking_completed"
EMAIL_TYPE_NPS_SURVEY = "nps_survey"
EMAIL_TYPE_INVOICE_SENT = "invoice_sent"
EMAIL_TYPE_INVOICE_OVERDUE = "invoice_overdue"
REMINDER_STATUSES = {"CONFIRMED", "PENDING"}
SCOPE_MARKETING = "marketing"
SCOPE_NPS = "nps"
EMAIL_SCOPES = {
    EMAIL_TYPE_BOOKING_COMPLETED: SCOPE_MARKETING,
    EMAIL_TYPE_NPS_SURVEY: SCOPE_NPS,
}


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _dedupe_key(
    email_type: str,
    recipient: str,
    *,
    booking_id: str | None = None,
    invoice_id: str | None = None,
    created_at: datetime | None = None,
) -> str:
    normalized_recipient = _normalize_email(recipient)
    if invoice_id:
        return f"invoice:{invoice_id}:{email_type}:{normalized_recipient}"
    if booking_id:
        return f"booking:{booking_id}:{email_type}:{normalized_recipient}"
    day = (created_at or datetime.now(tz=timezone.utc)).date().isoformat()
    return f"generic:{email_type}:{normalized_recipient}:{day}"


def _public_base_url() -> str | None:
    base = settings.public_base_url or settings.client_portal_base_url
    if not base:
        return None
    return base.rstrip("/")


def _unsubscribe_secret() -> str:
    return settings.email_unsubscribe_secret or settings.auth_secret_key


def issue_unsubscribe_token(email: str, scope: str, org_id: str) -> str:
    expires_at = datetime.now(tz=timezone.utc) + timedelta(minutes=settings.email_unsubscribe_ttl_minutes)
    payload = {
        "email": _normalize_email(email),
        "scope": scope,
        "org_id": str(org_id),
        "exp": int(expires_at.timestamp()),
    }
    raw = json.dumps(payload, separators=",:")
    signature = hmac.new(_unsubscribe_secret().encode(), raw.encode(), hashlib.sha256).digest()
    token_bytes = raw.encode() + b"." + signature
    return base64.urlsafe_b64encode(token_bytes).decode()


def verify_unsubscribe_token(token: str) -> dict:
    try:
        decoded = base64.urlsafe_b64decode(token.encode())
    except Exception as exc:  # noqa: BLE001
        raise ValueError("token_decode_failed") from exc
    try:
        raw, sig = decoded.rsplit(b".", 1)
    except ValueError as exc:  # noqa: BLE001
        raise ValueError("token_missing_sig") from exc
    expected_sig = hmac.new(_unsubscribe_secret().encode(), raw, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected_sig):
        raise ValueError("token_signature_mismatch")
    data = json.loads(raw.decode())
    if data.get("exp") and int(data["exp"]) < int(datetime.now(tz=timezone.utc).timestamp()):
        raise ValueError("token_expired")
    return data


def _format_start_time(booking: Booking) -> str:
    starts_at = booking.starts_at
    if starts_at.tzinfo is None:
        starts_at = starts_at.replace(tzinfo=timezone.utc)
    return starts_at.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M %Z")


async def _is_unsubscribed(session: AsyncSession, recipient: str, scope: str, org_id) -> bool:
    stmt = select(Unsubscribe.id).where(
        Unsubscribe.recipient == _normalize_email(recipient),
        Unsubscribe.scope == scope,
        Unsubscribe.org_id == org_id,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def _record_unsubscribe(session: AsyncSession, recipient: str, scope: str, org_id) -> None:
    base_stmt = insert(Unsubscribe).values(
        recipient=_normalize_email(recipient), scope=scope, org_id=org_id
    )
    dialect = session.get_bind().dialect.name if session.get_bind() else ""
    if hasattr(base_stmt, "on_conflict_do_nothing"):
        stmt = base_stmt.on_conflict_do_nothing(constraint="uq_unsubscribe_recipient_scope")
        await session.execute(stmt)
        return
    if dialect == "sqlite":
        await session.execute(base_stmt.prefix_with("OR IGNORE"))
        return
    await session.execute(base_stmt)


async def register_unsubscribe(session: AsyncSession, recipient: str, scope: str, org_id) -> None:
    await _record_unsubscribe(session, recipient, scope, org_id)
    await session.commit()


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
    review_link = None
    base = _public_base_url()
    if base:
        review_link = f"{base}/reviews" if base else None
    body_parts = [
        f"Hi {lead.name},\n\n",
        "Thanks for letting us clean your place. If you have a moment, we'd love a quick review.\n\n",
    ]
    if review_link:
        body_parts.append(f"Review link: {review_link}\n\n")
    body_parts.append("If anything was missed, reply so we can make it right.")
    body = "".join(body_parts)
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


def _unsubscribe_link(recipient: str, scope: str, org_id) -> str | None:
    base = _public_base_url()
    if not base:
        return None
    token = issue_unsubscribe_token(recipient, scope, org_id)
    return f"{base}/unsubscribe?token={token}"


def _with_unsubscribe(body: str, unsubscribe_url: str | None) -> str:
    if not unsubscribe_url:
        return body
    return body + "\n\nTo stop these messages, unsubscribe: " + unsubscribe_url


async def _try_send_email(
    adapter: EmailAdapter | None,
    recipient: str,
    subject: str,
    body: str,
    *,
    context: dict | None = None,
    headers: dict[str, str] | None = None,
) -> bool:
    if adapter is None:
        logger.warning("email_adapter_missing", extra={"extra": context or {}})
        return False
    try:
        delivered = await adapter.send_email(
            recipient=recipient, subject=subject, body=body, headers=headers
        )
        return bool(delivered)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "email_send_failed",
            extra={"extra": {**(context or {}), "reason": type(exc).__name__}},
        )
        return False


def _next_retry_at(attempt: int) -> datetime:
    delay = settings.email_retry_backoff_seconds * max(1, 2 ** (attempt - 1))
    return datetime.now(tz=timezone.utc) + timedelta(seconds=delay)


async def _record_failure(
    session: AsyncSession,
    *,
    event_id: str | None,
    dedupe_key: str,
    email_type: str,
    recipient: str,
    subject: str,
    body: str,
    booking_id: str | None,
    invoice_id: str | None,
    org_id,
    error: str,
) -> None:
    existing_stmt = select(EmailFailure).where(
        EmailFailure.org_id == org_id,
        EmailFailure.dedupe_key == dedupe_key,
        EmailFailure.status == "pending",
    )
    existing = (await session.execute(existing_stmt)).scalar_one_or_none()
    attempt = (existing.attempt_count if existing else 0) + 1
    status = "pending"
    if attempt >= settings.email_max_retries:
        status = "dead"
    values = {
        "email_event_id": event_id,
        "dedupe_key": dedupe_key,
        "email_type": email_type,
        "recipient": _normalize_email(recipient),
        "subject": subject,
        "body": body,
        "booking_id": booking_id,
        "invoice_id": invoice_id,
        "attempt_count": attempt,
        "max_retries": settings.email_max_retries,
        "status": status,
        "last_error": error,
        "next_retry_at": None if status == "dead" else _next_retry_at(attempt),
        "org_id": org_id,
    }
    if existing:
        await session.execute(
            update(EmailFailure)
            .where(EmailFailure.failure_id == existing.failure_id)
            .values(**values)
        )
    else:
        await session.execute(insert(EmailFailure).values(**values))


async def _reserve_email_event(
    session: AsyncSession,
    *,
    email_type: str,
    recipient: str,
    subject: str,
    body: str,
    booking_id: str | None,
    invoice_id: str | None,
    org_id,
    dedupe_key: str | None = None,
) -> tuple[str | None, str]:
    dedupe_key = dedupe_key or _dedupe_key(
        email_type, recipient, booking_id=booking_id, invoice_id=invoice_id
    )
    stmt = insert(EmailEvent).values(
        booking_id=booking_id,
        invoice_id=invoice_id,
        email_type=email_type,
        recipient=recipient,
        subject=subject,
        body=body,
        org_id=org_id,
        dedupe_key=dedupe_key,
    )
    bind = session.get_bind()
    dialect = bind.dialect.name if bind else ""
    if hasattr(stmt, "on_conflict_do_nothing"):
        stmt = stmt.on_conflict_do_nothing(constraint="uq_email_events_org_dedupe")
    elif dialect == "sqlite":
        stmt = stmt.prefix_with("OR IGNORE")

    try:
        result = await session.execute(stmt.returning(EmailEvent.event_id))
    except IntegrityError:
        await session.rollback()
        return None, dedupe_key

    event_id = result.scalar_one_or_none()
    if event_id is None:
        await session.rollback()
        return None, dedupe_key

    return event_id, dedupe_key


async def _send_with_record(
    session: AsyncSession,
    adapter: EmailAdapter | None,
    booking: Booking,
    lead: Lead,
    email_type: str,
    render: Callable[[Booking, Lead], tuple[str, str]],
    dedupe: bool = True,
    *,
    invoice_id: str | None = None,
) -> bool:
    if settings.email_mode == "off":
        return False
    if not lead.email:
        return False
    subject, body = render(booking, lead)
    org_id = getattr(booking, "org_id", settings.default_org_id)
    scope = EMAIL_SCOPES.get(email_type)
    if scope and await _is_unsubscribed(session, lead.email, scope, org_id):
        return False
    unsubscribe_url = _unsubscribe_link(lead.email, scope, org_id) if scope else None
    headers = {"List-Unsubscribe": f"<{unsubscribe_url}>"} if unsubscribe_url else None
    if scope:
        body = _with_unsubscribe(body, unsubscribe_url)

    dedupe_key = None
    if dedupe:
        dedupe_key = _dedupe_key(
            email_type, lead.email, booking_id=booking.booking_id, invoice_id=invoice_id
        )
    else:
        dedupe_key = f"manual:{email_type}:{booking.booking_id}:{_normalize_email(lead.email)}:{uuid.uuid4().hex}"

    event_id, dedupe_key = await _reserve_email_event(
        session,
        email_type=email_type,
        recipient=lead.email,
        subject=subject,
        body=body,
        booking_id=booking.booking_id,
        invoice_id=invoice_id,
        org_id=org_id,
        dedupe_key=dedupe_key,
    )
    if event_id is None:
        return False

    await session.commit()

    delivered = await _try_send_email(
        adapter,
        lead.email,
        subject,
        body,
        context={"booking_id": booking.booking_id, "email_type": email_type},
        headers=headers,
    )
    if delivered:
        return True

    await _record_failure(
        session,
        event_id=event_id,
        dedupe_key=dedupe_key,
        email_type=email_type,
        recipient=lead.email,
        subject=subject,
        body=body,
        booking_id=booking.booking_id,
        invoice_id=invoice_id,
        org_id=org_id,
        error="send_failed",
    )
    await session.commit()
    return False


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


def _format_money(cents: int, currency: str) -> str:
    dollars = cents / 100
    return f"{currency.upper()} {dollars:,.2f}"


def _render_invoice_sent(invoice: Invoice, lead: Lead, public_link: str, pdf_link: str) -> tuple[str, str]:
    subject = f"Invoice {invoice.invoice_number}"
    body = (
        f"Hi {lead.name},\n\n"
        f"Here's your invoice ({invoice.invoice_number}).\n"
        f"View online: {public_link}\n"
        f"Download PDF: {pdf_link}\n"
        f"Total due: {_format_money(invoice.total_cents, invoice.currency)}\n\n"
        "If you have questions, reply to this email."
    )
    return subject, body


def _render_invoice_overdue(invoice: Invoice, lead: Lead, public_link: str) -> tuple[str, str]:
    due_label = invoice.due_date.isoformat() if invoice.due_date else "your due date"
    balance_cents = getattr(invoice, "balance_due_cents", None)
    if balance_cents is None:
        balance_cents = invoice_service.outstanding_balance_cents(invoice)
    subject = f"Invoice {invoice.invoice_number} is overdue"
    body = (
        f"Hi {lead.name},\n\n"
        f"Our records show invoice {invoice.invoice_number} was due on {due_label} and still has a balance.\n"
        f"View and pay online: {public_link}\n"
        f"Balance: {_format_money(balance_cents, invoice.currency)}\n\n"
        "If you've already paid, please ignore this message or reply with the receipt so we can update our records."
    )
    return subject, body


async def send_invoice_sent_email(
    session: AsyncSession,
    adapter: EmailAdapter | None,
    invoice: Invoice,
    lead: Lead,
    *,
    public_link: str,
    public_link_pdf: str,
) -> bool:
    if settings.email_mode == "off":
        return False
    if not lead.email:
        return False
    subject, body = _render_invoice_sent(invoice, lead, public_link, public_link_pdf)
    org_id = getattr(invoice, "org_id", settings.default_org_id)
    event_id, dedupe_key = await _reserve_email_event(
        session,
        email_type=EMAIL_TYPE_INVOICE_SENT,
        recipient=lead.email,
        subject=subject,
        body=body,
        booking_id=invoice.order_id,
        invoice_id=invoice.invoice_id,
        org_id=org_id,
    )
    if event_id is None:
        return False
    await session.commit()
    delivered = await _try_send_email(
        adapter,
        lead.email,
        subject,
        body,
        context={"invoice_id": invoice.invoice_id, "email_type": EMAIL_TYPE_INVOICE_SENT},
    )
    if delivered:
        return True
    await _record_failure(
        session,
        event_id=event_id,
        dedupe_key=dedupe_key,
        email_type=EMAIL_TYPE_INVOICE_SENT,
        recipient=lead.email,
        subject=subject,
        body=body,
        booking_id=invoice.order_id,
        invoice_id=invoice.invoice_id,
        org_id=org_id,
        error="send_failed",
    )
    await session.commit()
    return False


async def send_invoice_overdue_email(
    session: AsyncSession,
    adapter: EmailAdapter | None,
    invoice: Invoice,
    lead: Lead,
    *,
    public_link: str,
) -> bool:
    if settings.email_mode == "off":
        return False
    if not lead.email:
        return False
    subject, body = _render_invoice_overdue(invoice, lead, public_link)
    org_id = getattr(invoice, "org_id", settings.default_org_id)
    event_id, dedupe_key = await _reserve_email_event(
        session,
        email_type=EMAIL_TYPE_INVOICE_OVERDUE,
        recipient=lead.email,
        subject=subject,
        body=body,
        booking_id=invoice.order_id,
        invoice_id=invoice.invoice_id,
        org_id=org_id,
    )
    if event_id is None:
        return False
    await session.commit()
    delivered = await _try_send_email(
        adapter,
        lead.email,
        subject,
        body,
        context={"invoice_id": invoice.invoice_id, "email_type": EMAIL_TYPE_INVOICE_OVERDUE},
    )
    if delivered:
        return True
    await _record_failure(
        session,
        event_id=event_id,
        dedupe_key=dedupe_key,
        email_type=EMAIL_TYPE_INVOICE_OVERDUE,
        recipient=lead.email,
        subject=subject,
        body=body,
        booking_id=invoice.order_id,
        invoice_id=invoice.invoice_id,
        org_id=org_id,
        error="send_failed",
    )
    await session.commit()
    return False


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

    manual_dedupe = f"manual_resend:{event.event_id}:{uuid.uuid4().hex}"
    org_id = getattr(event, "org_id", settings.default_org_id)
    event_id, _ = await _reserve_email_event(
        session,
        email_type=event.email_type,
        recipient=event.recipient,
        subject=event.subject,
        body=event.body,
        booking_id=booking_id,
        invoice_id=event.invoice_id,
        org_id=org_id,
        dedupe_key=manual_dedupe,
    )
    if event_id:
        await session.commit()
    return {"booking_id": booking_id, "email_type": event.email_type, "recipient": event.recipient}


async def retry_email_failures(session: AsyncSession, adapter: EmailAdapter | None) -> dict[str, int]:
    now = datetime.now(tz=timezone.utc)
    stmt = (
        select(EmailFailure)
        .where(EmailFailure.status == "pending", EmailFailure.next_retry_at <= now)
        .order_by(EmailFailure.next_retry_at)
        .limit(100)
    )
    result = await session.execute(stmt)
    failures = result.scalars().all()
    sent = 0
    dead = 0
    for failure in failures:
        scope = EMAIL_SCOPES.get(failure.email_type)
        if scope and await _is_unsubscribed(session, failure.recipient, scope, failure.org_id):
            await session.execute(
                update(EmailFailure)
                .where(EmailFailure.failure_id == failure.failure_id)
                .values(status="dead", last_error="unsubscribed", next_retry_at=None)
            )
            dead += 1
            continue
        delivered = await _try_send_email(
            adapter,
            failure.recipient,
            failure.subject,
            failure.body,
            context={"email_type": failure.email_type, "failure_id": failure.failure_id},
        )
        if delivered:
            await session.execute(
                update(EmailFailure)
                .where(EmailFailure.failure_id == failure.failure_id)
                .values(
                    status="sent",
                    last_error=None,
                    next_retry_at=None,
                    attempt_count=failure.attempt_count + 1,
                )
            )
            sent += 1
            continue

        attempt = failure.attempt_count + 1
        status = "dead" if attempt >= failure.max_retries else "pending"
        next_retry = None if status == "dead" else _next_retry_at(attempt)
        await session.execute(
            update(EmailFailure)
            .where(EmailFailure.failure_id == failure.failure_id)
            .values(
                attempt_count=attempt,
                status=status,
                next_retry_at=next_retry,
                last_error="send_failed",
            )
        )
        if status == "dead":
            dead += 1

    await session.commit()
    return {"sent": sent, "dead": dead, "checked": len(failures)}
