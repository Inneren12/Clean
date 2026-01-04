"""Service layer for operator work queues."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.bookings.db_models import Booking, OrderPhoto
from app.domain.export_events.db_models import ExportEvent
from app.domain.invoices.db_models import Invoice
from app.domain.leads.db_models import Lead
from app.domain.outbox.db_models import OutboxEvent
from app.domain.queues.schemas import (
    AssignmentQueueItem,
    DLQItem,
    InvoiceQueueItem,
    PhotoQueueItem,
    QuickActionItem,
)
from app.domain.workers.db_models import Worker
from app.domain.bookings.db_models import Team

logger = logging.getLogger(__name__)


async def list_photo_queue(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    status_filter: Literal["pending", "needs_retake", "all"] = "all",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[PhotoQueueItem], int, dict[str, int]]:
    """Fetch photos requiring review.

    Returns:
        (items, total, counts) where counts = {pending, needs_retake}
    """
    # Build base query with explicit org_id constraints on all joins
    stmt = (
        select(OrderPhoto, Booking, Worker)
        .join(
            Booking,
            and_(
                OrderPhoto.order_id == Booking.booking_id,
                Booking.org_id == org_id,
            ),
        )
        .outerjoin(
            Worker,
            and_(
                Booking.assigned_worker_id == Worker.worker_id,
                Worker.org_id == org_id,
            ),
        )
        .where(OrderPhoto.org_id == org_id)
    )

    # Apply status filter
    if status_filter == "pending":
        stmt = stmt.where(OrderPhoto.review_status == "PENDING")
    elif status_filter == "needs_retake":
        stmt = stmt.where(OrderPhoto.needs_retake == True)  # noqa: E712

    # Count totals
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await session.execute(count_stmt)).scalar_one()

    # Get counts by category
    pending_stmt = (
        select(func.count())
        .select_from(OrderPhoto)
        .where(OrderPhoto.org_id == org_id, OrderPhoto.review_status == "PENDING")
    )
    pending_count = (await session.execute(pending_stmt)).scalar_one()

    retake_stmt = (
        select(func.count())
        .select_from(OrderPhoto)
        .where(OrderPhoto.org_id == org_id, OrderPhoto.needs_retake == True)  # noqa: E712
    )
    retake_count = (await session.execute(retake_stmt)).scalar_one()

    # Fetch paginated items
    stmt = stmt.order_by(OrderPhoto.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    rows = result.all()

    items = []
    for photo, booking, worker in rows:
        items.append(
            PhotoQueueItem(
                photo_id=photo.photo_id,
                order_id=photo.order_id,
                booking_ref=booking.booking_id if booking else None,
                worker_name=worker.name if worker else None,
                phase=photo.phase,
                review_status=photo.review_status,
                needs_retake=photo.needs_retake,
                uploaded_at=photo.created_at,
                filename=photo.filename,
                content_type=photo.content_type,
                size_bytes=photo.size_bytes,
                quick_actions=[
                    QuickActionItem(label="View Photo", target=f"/v1/orders/{photo.order_id}/photos/{photo.photo_id}"),
                    QuickActionItem(label="Approve", target=f"/v1/orders/{photo.order_id}/photos/{photo.photo_id}/review", method="POST"),
                    QuickActionItem(label="View Booking", target=f"/v1/bookings/{booking.booking_id}" if booking else "#"),
                ],
            )
        )

    counts = {"pending": pending_count, "needs_retake": retake_count}
    return items, total, counts


async def list_invoice_queue(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    status_filter: Literal["overdue", "unpaid", "all"] = "all",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[InvoiceQueueItem], int, dict[str, int]]:
    """Fetch invoices requiring attention (overdue or unpaid).

    Returns:
        (items, total, counts) where counts = {overdue, unpaid}
    """
    today = datetime.now(timezone.utc).date()

    # Build base query with explicit org_id constraints on joins
    stmt = (
        select(Invoice, Lead)
        .outerjoin(
            Lead,
            and_(
                Invoice.customer_id == Lead.lead_id,
                Lead.org_id == org_id,
            ),
        )
        .where(Invoice.org_id == org_id)
    )

    # Apply status filter
    if status_filter == "overdue":
        stmt = stmt.where(
            and_(
                Invoice.status.in_(["SENT", "OVERDUE"]),
                Invoice.due_date < today,
            )
        )
    elif status_filter == "unpaid":
        stmt = stmt.where(Invoice.status.in_(["DRAFT", "SENT", "OVERDUE"]))
    else:
        # All - show anything not paid/cancelled
        stmt = stmt.where(Invoice.status.in_(["DRAFT", "SENT", "OVERDUE"]))

    # Count totals
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await session.execute(count_stmt)).scalar_one()

    # Get counts by category
    overdue_stmt = (
        select(func.count())
        .select_from(Invoice)
        .where(
            Invoice.org_id == org_id,
            Invoice.status.in_(["SENT", "OVERDUE"]),
            Invoice.due_date < today,
        )
    )
    overdue_count = (await session.execute(overdue_stmt)).scalar_one()

    unpaid_stmt = (
        select(func.count())
        .select_from(Invoice)
        .where(Invoice.org_id == org_id, Invoice.status.in_(["DRAFT", "SENT", "OVERDUE"]))
    )
    unpaid_count = (await session.execute(unpaid_stmt)).scalar_one()

    # Fetch paginated items
    stmt = stmt.order_by(Invoice.due_date.asc().nullsfirst(), Invoice.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    rows = result.all()

    items = []
    for invoice, lead in rows:
        days_overdue = None
        if invoice.due_date and invoice.due_date < today:
            days_overdue = (today - invoice.due_date).days

        items.append(
            InvoiceQueueItem(
                invoice_id=invoice.invoice_id,
                invoice_number=invoice.invoice_number,
                order_id=invoice.order_id,
                customer_name=lead.name if lead else None,
                customer_email=lead.email if lead else None,
                status=invoice.status,
                due_date=datetime.combine(invoice.due_date, datetime.min.time(), tzinfo=timezone.utc) if invoice.due_date else None,
                total_cents=invoice.total_cents,
                currency=invoice.currency,
                days_overdue=days_overdue,
                created_at=invoice.created_at,
                quick_actions=[
                    QuickActionItem(label="View Invoice", target=f"/v1/invoices/{invoice.invoice_id}"),
                    QuickActionItem(label="Resend", target=f"/v1/admin/invoices/{invoice.invoice_id}/resend", method="POST"),
                    QuickActionItem(label="Mark Paid", target=f"/v1/admin/invoices/{invoice.invoice_id}/mark-paid", method="POST"),
                ],
            )
        )

    counts = {"overdue": overdue_count, "unpaid": unpaid_count}
    return items, total, counts


async def list_assignment_queue(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    days_ahead: int = 7,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[AssignmentQueueItem], int, dict[str, int]]:
    """Fetch unassigned bookings in the next N days.

    Returns:
        (items, total, counts) where counts = {urgent} (within 24h)
    """
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=days_ahead)
    urgent_cutoff = now + timedelta(hours=24)

    # Build base query - unassigned bookings with explicit org_id constraints on joins
    stmt = (
        select(Booking, Lead, Team)
        .outerjoin(
            Lead,
            and_(
                Booking.lead_id == Lead.lead_id,
                Lead.org_id == org_id,
            ),
        )
        .join(
            Team,
            and_(
                Booking.team_id == Team.team_id,
                Team.org_id == org_id,
            ),
        )
        .where(
            Booking.org_id == org_id,
            Booking.assigned_worker_id.is_(None),
            Booking.starts_at >= now,
            Booking.starts_at <= cutoff,
            Booking.status.in_(["PENDING", "CONFIRMED"]),
        )
    )

    # Count totals
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await session.execute(count_stmt)).scalar_one()

    # Count urgent (within 24h)
    urgent_stmt = (
        select(func.count())
        .select_from(Booking)
        .where(
            Booking.org_id == org_id,
            Booking.assigned_worker_id.is_(None),
            Booking.starts_at >= now,
            Booking.starts_at <= urgent_cutoff,
            Booking.status.in_(["PENDING", "CONFIRMED"]),
        )
    )
    urgent_count = (await session.execute(urgent_stmt)).scalar_one()

    # Fetch paginated items (sort by start time - earliest first)
    stmt = stmt.order_by(Booking.starts_at.asc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    rows = result.all()

    items = []
    for booking, lead, team in rows:
        days_until = (booking.starts_at - now).days

        items.append(
            AssignmentQueueItem(
                booking_id=booking.booking_id,
                lead_name=lead.name if lead else None,
                lead_phone=lead.phone if lead else None,
                lead_email=lead.email if lead else None,
                starts_at=booking.starts_at,
                duration_minutes=booking.duration_minutes,
                status=booking.status,
                team_name=team.name if team else "Unknown",
                created_at=booking.created_at,
                days_until_start=days_until,
                quick_actions=[
                    QuickActionItem(label="View Booking", target=f"/v1/bookings/{booking.booking_id}"),
                    QuickActionItem(label="Assign Worker", target=f"/v1/admin/bookings/{booking.booking_id}/assign", method="POST"),
                    QuickActionItem(label="View Schedule", target=f"/v1/admin/schedule?date={booking.starts_at.date()}"),
                ],
            )
        )

    counts = {"urgent": urgent_count}
    return items, total, counts


async def list_dlq(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    kind_filter: Literal["outbox", "export", "all"] = "all",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[DLQItem], int, dict[str, int]]:
    """Fetch dead letter queue items (failed outbox + export events).

    Uses SQL-level pagination with UNION ALL for scalability.

    Returns:
        (items, total, counts) where counts = {outbox_dead, export_dead}
    """
    # Get counts first (always fetch both for display)
    outbox_count_stmt = (
        select(func.count())
        .select_from(OutboxEvent)
        .where(OutboxEvent.org_id == org_id, OutboxEvent.status == "dead")
    )
    outbox_dead_count = (await session.execute(outbox_count_stmt)).scalar_one()

    export_count_stmt = (
        select(func.count())
        .select_from(ExportEvent)
        .where(
            ExportEvent.org_id == org_id,
            ExportEvent.last_error_code.is_not(None),
        )
    )
    export_dead_count = (await session.execute(export_count_stmt)).scalar_one()

    items = []

    # Handle single-kind filters with SQL pagination
    if kind_filter == "outbox":
        outbox_stmt = (
            select(OutboxEvent)
            .where(OutboxEvent.org_id == org_id, OutboxEvent.status == "dead")
            .order_by(OutboxEvent.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        outbox_result = await session.execute(outbox_stmt)
        outbox_events = outbox_result.scalars().all()

        for event in outbox_events:
            payload_summary = _summarize_payload(event.kind, event.payload_json)
            items.append(
                DLQItem(
                    event_id=event.event_id,
                    kind="outbox",
                    event_type=event.kind,
                    org_id=str(event.org_id),
                    status=event.status,
                    attempts=event.attempts,
                    last_error=event.last_error,
                    created_at=event.created_at,
                    payload_summary=payload_summary,
                    quick_actions=[
                        QuickActionItem(label="Replay", target=f"/v1/admin/outbox/{event.event_id}/replay", method="POST"),
                        QuickActionItem(label="View Details", target=f"/v1/admin/outbox/{event.event_id}"),
                    ],
                )
            )
        total = outbox_dead_count

    elif kind_filter == "export":
        export_stmt = (
            select(ExportEvent)
            .where(
                ExportEvent.org_id == org_id,
                ExportEvent.last_error_code.is_not(None),
            )
            .order_by(ExportEvent.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        export_result = await session.execute(export_stmt)
        export_events = export_result.scalars().all()

        for event in export_events:
            payload_summary = f"Lead export ({event.mode}): {event.target_url_host or 'unknown'}"
            items.append(
                DLQItem(
                    event_id=event.event_id,
                    kind="export",
                    event_type=event.mode,
                    org_id=str(event.org_id),
                    status=f"error_{event.last_error_code}" if event.last_error_code else "failed",
                    attempts=event.attempts,
                    last_error=event.last_error_code,
                    created_at=event.created_at,
                    payload_summary=payload_summary,
                    quick_actions=[
                        QuickActionItem(label="Replay", target=f"/v1/admin/export-dead-letter/{event.event_id}/replay", method="POST"),
                        QuickActionItem(label="View Details", target=f"/v1/admin/export-dead-letter/{event.event_id}"),
                    ],
                )
            )
        total = export_dead_count

    else:
        # kind_filter == "all": Use SQL UNION ALL for combined pagination
        # Create aligned subqueries for UNION ALL
        from sqlalchemy import literal, union_all

        # Outbox subquery: select needed fields + discriminator
        outbox_subq = (
            select(
                OutboxEvent.event_id.label("event_id"),
                literal("outbox").label("kind"),
                OutboxEvent.kind.label("event_type"),
                OutboxEvent.org_id.label("org_id"),
                OutboxEvent.status.label("status"),
                OutboxEvent.attempts.label("attempts"),
                OutboxEvent.last_error.label("last_error"),
                OutboxEvent.created_at.label("created_at"),
                OutboxEvent.payload_json.label("payload_json"),
                literal(None).label("mode"),
                literal(None).label("target_url_host"),
            )
            .where(OutboxEvent.org_id == org_id, OutboxEvent.status == "dead")
        )

        # Export subquery: select aligned fields
        export_subq = (
            select(
                ExportEvent.event_id.label("event_id"),
                literal("export").label("kind"),
                ExportEvent.mode.label("event_type"),
                ExportEvent.org_id.label("org_id"),
                literal("failed").label("status"),
                ExportEvent.attempts.label("attempts"),
                ExportEvent.last_error_code.label("last_error"),
                ExportEvent.created_at.label("created_at"),
                literal(None).label("payload_json"),
                ExportEvent.mode.label("mode"),
                ExportEvent.target_url_host.label("target_url_host"),
            )
            .where(
                ExportEvent.org_id == org_id,
                ExportEvent.last_error_code.is_not(None),
            )
        )

        # UNION ALL and apply ordering + pagination at SQL level
        combined_stmt = (
            union_all(outbox_subq, export_subq)
            .subquery()
        )

        paginated_stmt = (
            select(combined_stmt)
            .order_by(combined_stmt.c.created_at.desc())
            .limit(limit)
            .offset(offset)
        )

        result = await session.execute(paginated_stmt)
        rows = result.all()

        for row in rows:
            if row.kind == "outbox":
                payload_summary = _summarize_payload(row.event_type, row.payload_json or {})
                items.append(
                    DLQItem(
                        event_id=row.event_id,
                        kind="outbox",
                        event_type=row.event_type,
                        org_id=str(row.org_id),
                        status=row.status,
                        attempts=row.attempts,
                        last_error=row.last_error,
                        created_at=row.created_at,
                        payload_summary=payload_summary,
                        quick_actions=[
                            QuickActionItem(label="Replay", target=f"/v1/admin/outbox/{row.event_id}/replay", method="POST"),
                            QuickActionItem(label="View Details", target=f"/v1/admin/outbox/{row.event_id}"),
                        ],
                    )
                )
            else:  # export
                payload_summary = f"Lead export ({row.mode}): {row.target_url_host or 'unknown'}"
                items.append(
                    DLQItem(
                        event_id=row.event_id,
                        kind="export",
                        event_type=row.event_type,
                        org_id=str(row.org_id),
                        status=f"error_{row.last_error}" if row.last_error else "failed",
                        attempts=row.attempts,
                        last_error=row.last_error,
                        created_at=row.created_at,
                        payload_summary=payload_summary,
                        quick_actions=[
                            QuickActionItem(label="Replay", target=f"/v1/admin/export-dead-letter/{row.event_id}/replay", method="POST"),
                            QuickActionItem(label="View Details", target=f"/v1/admin/export-dead-letter/{row.event_id}"),
                        ],
                    )
                )

        total = outbox_dead_count + export_dead_count

    counts = {"outbox_dead": outbox_dead_count, "export_dead": export_dead_count}
    return items, total, counts


def _summarize_payload(kind: str, payload: dict) -> str:
    """Create human-readable summary of outbox payload."""
    if kind == "email":
        recipient = payload.get("recipient", "unknown")
        subject = payload.get("subject", "")
        return f"Email to {recipient}: {subject[:50]}"
    elif kind == "webhook":
        url = payload.get("url", "unknown")
        return f"Webhook to {url}"
    elif kind == "export":
        export_type = payload.get("export_type", "unknown")
        return f"Export: {export_type}"
    return f"{kind}: {str(payload)[:50]}"
