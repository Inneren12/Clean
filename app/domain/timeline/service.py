"""Service layer for unified timeline view."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.admin_audit.db_models import AdminAuditLog
from app.domain.bookings.db_models import Booking, EmailEvent, OrderPhoto
from app.domain.invoices.db_models import Invoice, Payment
from app.domain.nps.db_models import NpsResponse, SupportTicket
from app.domain.outbox.db_models import OutboxEvent
from app.domain.timeline.schemas import TimelineEvent

logger = logging.getLogger(__name__)


async def get_booking_timeline(
    session: AsyncSession, org_id: uuid.UUID, booking_id: str
) -> list[TimelineEvent]:
    """Fetch unified timeline for a booking.

    Includes:
    - Audit logs
    - Email events
    - Payment events
    - Photo reviews
    - NPS responses
    - Support tickets
    - Outbox events
    """
    events: list[TimelineEvent] = []

    # Fetch admin audit logs for this booking
    audit_stmt = (
        select(AdminAuditLog)
        .where(
            AdminAuditLog.org_id == org_id,
            or_(
                AdminAuditLog.resource_id == booking_id,
                AdminAuditLog.resource_id.like(f"%{booking_id}%"),  # For related resources
            ),
        )
        .order_by(AdminAuditLog.created_at.desc())
    )
    audit_logs = (await session.execute(audit_stmt)).scalars().all()
    for audit in audit_logs:
        events.append(
            TimelineEvent(
                event_id=audit.audit_id,
                event_type="audit_log",
                timestamp=audit.created_at,
                actor=audit.actor,
                action=audit.action,
                resource_type=audit.resource_type,
                resource_id=audit.resource_id,
                before=audit.before,
                after=audit.after,
                metadata={"role": audit.role},
            )
        )

    # Fetch email events for this booking
    email_stmt = (
        select(EmailEvent)
        .where(EmailEvent.org_id == org_id, EmailEvent.booking_id == booking_id)
        .order_by(EmailEvent.created_at.desc())
    )
    email_events = (await session.execute(email_stmt)).scalars().all()
    for email in email_events:
        events.append(
            TimelineEvent(
                event_id=email.event_id,
                event_type="email_sent",
                timestamp=email.created_at,
                actor="system",
                action=f"Sent {email.email_type} email to {email.recipient}",
                resource_type="booking",
                resource_id=booking_id,
                metadata={
                    "email_type": email.email_type,
                    "recipient": email.recipient,
                    "subject": email.subject,
                },
            )
        )

    # Fetch payments for this booking
    payment_stmt = (
        select(Payment)
        .where(Payment.org_id == org_id, Payment.booking_id == booking_id)
        .order_by(Payment.created_at.desc())
    )
    payments = (await session.execute(payment_stmt)).scalars().all()
    for payment in payments:
        events.append(
            TimelineEvent(
                event_id=payment.payment_id,
                event_type="payment_received",
                timestamp=payment.received_at or payment.created_at,
                actor=payment.provider,
                action=f"Payment {payment.status}: {payment.amount_cents / 100:.2f} {payment.currency}",
                resource_type="payment",
                resource_id=payment.payment_id,
                metadata={
                    "provider": payment.provider,
                    "amount_cents": payment.amount_cents,
                    "currency": payment.currency,
                    "status": payment.status,
                    "method": payment.method,
                },
            )
        )

    # Fetch photo reviews for this booking
    photo_stmt = (
        select(OrderPhoto)
        .where(OrderPhoto.org_id == org_id, OrderPhoto.order_id == booking_id)
        .order_by(OrderPhoto.created_at.desc())
    )
    photos = (await session.execute(photo_stmt)).scalars().all()
    for photo in photos:
        # Photo upload event
        events.append(
            TimelineEvent(
                event_id=f"{photo.photo_id}_upload",
                event_type="photo_reviewed",
                timestamp=photo.created_at,
                actor=photo.uploaded_by,
                action=f"Uploaded photo ({photo.phase})",
                resource_type="photo",
                resource_id=photo.photo_id,
                metadata={
                    "phase": photo.phase,
                    "filename": photo.filename,
                    "review_status": photo.review_status,
                },
            )
        )
        # Photo review event (if reviewed)
        if photo.reviewed_at:
            events.append(
                TimelineEvent(
                    event_id=f"{photo.photo_id}_review",
                    event_type="photo_reviewed",
                    timestamp=photo.reviewed_at,
                    actor=photo.reviewed_by,
                    action=f"Photo reviewed: {photo.review_status}",
                    resource_type="photo",
                    resource_id=photo.photo_id,
                    before={"review_status": "PENDING"},
                    after={"review_status": photo.review_status, "needs_retake": photo.needs_retake},
                    metadata={
                        "review_comment": photo.review_comment,
                        "needs_retake": photo.needs_retake,
                    },
                )
            )

    # Fetch NPS responses for this booking
    nps_stmt = (
        select(NpsResponse)
        .where(NpsResponse.order_id == booking_id)
        .order_by(NpsResponse.created_at.desc())
    )
    nps_responses = (await session.execute(nps_stmt)).scalars().all()
    for nps in nps_responses:
        events.append(
            TimelineEvent(
                event_id=nps.id,
                event_type="nps_response",
                timestamp=nps.created_at,
                actor="customer",
                action=f"NPS score: {nps.score}/10",
                resource_type="nps",
                resource_id=nps.id,
                metadata={
                    "score": nps.score,
                    "comment": nps.comment,
                },
            )
        )

    # Fetch support tickets for this booking
    ticket_stmt = (
        select(SupportTicket)
        .where(SupportTicket.order_id == booking_id)
        .order_by(SupportTicket.created_at.desc())
    )
    tickets = (await session.execute(ticket_stmt)).scalars().all()
    for ticket in tickets:
        events.append(
            TimelineEvent(
                event_id=ticket.id,
                event_type="support_ticket",
                timestamp=ticket.created_at,
                actor="customer",
                action=f"Support ticket created: {ticket.subject}",
                resource_type="support_ticket",
                resource_id=ticket.id,
                metadata={
                    "status": ticket.status,
                    "priority": ticket.priority,
                    "subject": ticket.subject,
                    "body": ticket.body[:200],  # Truncate
                },
            )
        )

    # Fetch outbox events related to this booking
    outbox_stmt = (
        select(OutboxEvent)
        .where(OutboxEvent.org_id == org_id, OutboxEvent.dedupe_key.like(f"%{booking_id}%"))
        .order_by(OutboxEvent.created_at.desc())
        .limit(20)  # Limit outbox events to avoid too many
    )
    outbox_events = (await session.execute(outbox_stmt)).scalars().all()
    for outbox in outbox_events:
        events.append(
            TimelineEvent(
                event_id=outbox.event_id,
                event_type="outbox_event",
                timestamp=outbox.created_at,
                actor="system",
                action=f"Outbox {outbox.kind}: {outbox.status}",
                resource_type="outbox",
                resource_id=outbox.event_id,
                metadata={
                    "kind": outbox.kind,
                    "status": outbox.status,
                    "attempts": outbox.attempts,
                    "last_error": outbox.last_error,
                },
            )
        )

    # Sort all events by timestamp (descending - most recent first)
    events.sort(key=lambda e: e.timestamp, reverse=True)
    return events


async def get_invoice_timeline(
    session: AsyncSession, org_id: uuid.UUID, invoice_id: str
) -> list[TimelineEvent]:
    """Fetch unified timeline for an invoice.

    Includes:
    - Audit logs
    - Email events
    - Payment events
    - Outbox events
    """
    events: list[TimelineEvent] = []

    # Fetch admin audit logs for this invoice
    audit_stmt = (
        select(AdminAuditLog)
        .where(
            AdminAuditLog.org_id == org_id,
            or_(
                AdminAuditLog.resource_id == invoice_id,
                AdminAuditLog.resource_id.like(f"%{invoice_id}%"),
            ),
        )
        .order_by(AdminAuditLog.created_at.desc())
    )
    audit_logs = (await session.execute(audit_stmt)).scalars().all()
    for audit in audit_logs:
        events.append(
            TimelineEvent(
                event_id=audit.audit_id,
                event_type="audit_log",
                timestamp=audit.created_at,
                actor=audit.actor,
                action=audit.action,
                resource_type=audit.resource_type,
                resource_id=audit.resource_id,
                before=audit.before,
                after=audit.after,
                metadata={"role": audit.role},
            )
        )

    # Fetch email events for this invoice
    email_stmt = (
        select(EmailEvent)
        .where(EmailEvent.org_id == org_id, EmailEvent.invoice_id == invoice_id)
        .order_by(EmailEvent.created_at.desc())
    )
    email_events = (await session.execute(email_stmt)).scalars().all()
    for email in email_events:
        events.append(
            TimelineEvent(
                event_id=email.event_id,
                event_type="email_sent",
                timestamp=email.created_at,
                actor="system",
                action=f"Sent {email.email_type} email to {email.recipient}",
                resource_type="invoice",
                resource_id=invoice_id,
                metadata={
                    "email_type": email.email_type,
                    "recipient": email.recipient,
                    "subject": email.subject,
                },
            )
        )

    # Fetch payments for this invoice
    payment_stmt = (
        select(Payment)
        .where(Payment.org_id == org_id, Payment.invoice_id == invoice_id)
        .order_by(Payment.created_at.desc())
    )
    payments = (await session.execute(payment_stmt)).scalars().all()
    for payment in payments:
        events.append(
            TimelineEvent(
                event_id=payment.payment_id,
                event_type="payment_received",
                timestamp=payment.received_at or payment.created_at,
                actor=payment.provider,
                action=f"Payment {payment.status}: {payment.amount_cents / 100:.2f} {payment.currency}",
                resource_type="payment",
                resource_id=payment.payment_id,
                metadata={
                    "provider": payment.provider,
                    "amount_cents": payment.amount_cents,
                    "currency": payment.currency,
                    "status": payment.status,
                    "method": payment.method,
                },
            )
        )

    # Fetch outbox events related to this invoice
    outbox_stmt = (
        select(OutboxEvent)
        .where(OutboxEvent.org_id == org_id, OutboxEvent.dedupe_key.like(f"%{invoice_id}%"))
        .order_by(OutboxEvent.created_at.desc())
        .limit(20)
    )
    outbox_events = (await session.execute(outbox_stmt)).scalars().all()
    for outbox in outbox_events:
        events.append(
            TimelineEvent(
                event_id=outbox.event_id,
                event_type="outbox_event",
                timestamp=outbox.created_at,
                actor="system",
                action=f"Outbox {outbox.kind}: {outbox.status}",
                resource_type="outbox",
                resource_id=outbox.event_id,
                metadata={
                    "kind": outbox.kind,
                    "status": outbox.status,
                    "attempts": outbox.attempts,
                    "last_error": outbox.last_error,
                },
            )
        )

    # Sort all events by timestamp (descending - most recent first)
    events.sort(key=lambda e: e.timestamp, reverse=True)
    return events
