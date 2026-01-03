from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.bookings.db_models import Booking, EmailEvent, Team, TeamBlackout
from app.domain.bookings.service import (
    BLOCKING_STATUSES,
    BUFFER_MINUTES,
    DEFAULT_SLOT_DURATION_MINUTES,
    ensure_default_team,
    generate_slots,
)
from app.domain.invoices.db_models import Invoice, Payment
from app.domain.leads.db_models import Lead
from app.domain.notifications import email_service

logger = logging.getLogger(__name__)


DANGEROUS_CSV_PREFIXES = ("=", "+", "-", "@", "\t")


def safe_csv_value(value: object) -> str:
    text = "" if value is None else str(value)
    if not text:
        return ""
    if text.startswith(DANGEROUS_CSV_PREFIXES):
        return f"'{text}"
    return text


@dataclass(slots=True)
class QuickAction:
    label: str
    target: str
    method: str = "GET"


@dataclass(slots=True)
class SearchHit:
    kind: str
    ref: str
    label: str
    status: str | None
    created_at: datetime
    quick_actions: list[QuickAction]


def _build_quick_actions(kind: str, ref: str) -> list[QuickAction]:
    if kind == "lead":
        return [QuickAction(label="View lead", target=f"/v1/admin/leads/{ref}")]
    if kind == "booking":
        return [
            QuickAction(label="View booking", target=f"/v1/bookings/{ref}"),
            QuickAction(label="Move", target=f"/v1/admin/schedule/{ref}/move", method="POST"),
        ]
    if kind == "invoice":
        return [QuickAction(label="View invoice", target=f"/v1/invoices/{ref}")]
    if kind == "payment":
        return [QuickAction(label="Review payment", target=f"/v1/admin/payments/{ref}")]
    return []


async def global_search(session: AsyncSession, org_id, q: str, limit: int = 20) -> list[SearchHit]:
    if not q:
        return []

    term = f"%{q.strip()}%"
    hits: list[SearchHit] = []

    lead_stmt: Select = (
        select(Lead)
        .where(Lead.org_id == org_id)
        .where(or_(Lead.name.ilike(term), Lead.email.ilike(term), Lead.phone.ilike(term)))
        .order_by(Lead.created_at.desc())
        .limit(limit)
    )
    for lead in (await session.execute(lead_stmt)).scalars().all():
        hits.append(
            SearchHit(
                kind="lead",
                ref=lead.lead_id,
                label=lead.name,
                status=lead.status,
                created_at=lead.created_at,
                quick_actions=_build_quick_actions("lead", lead.lead_id),
            )
        )

    booking_stmt: Select = (
        select(Booking)
        .where(Booking.org_id == org_id)
        .where(
            or_(
                Booking.booking_id.ilike(term),
                Booking.status.ilike(term),
            )
        )
        .order_by(Booking.created_at.desc())
        .limit(limit)
    )
    for booking in (await session.execute(booking_stmt)).scalars().all():
        hits.append(
            SearchHit(
                kind="booking",
                ref=booking.booking_id,
                label=f"Booking {booking.booking_id}",
                status=booking.status,
                created_at=booking.created_at,
                quick_actions=_build_quick_actions("booking", booking.booking_id),
            )
        )

    invoice_stmt: Select = (
        select(Invoice)
        .where(Invoice.org_id == org_id)
        .where(or_(Invoice.invoice_number.ilike(term), Invoice.invoice_id.ilike(term)))
        .order_by(Invoice.created_at.desc())
        .limit(limit)
    )
    for invoice in (await session.execute(invoice_stmt)).scalars().all():
        hits.append(
            SearchHit(
                kind="invoice",
                ref=invoice.invoice_id,
                label=invoice.invoice_number,
                status=invoice.status,
                created_at=invoice.created_at,
                quick_actions=_build_quick_actions("invoice", invoice.invoice_id),
            )
        )

    payment_stmt: Select = (
        select(Payment)
        .where(Payment.org_id == org_id)
        .where(
            or_(
                Payment.payment_id.ilike(term),
                Payment.provider_ref.ilike(term),
                Payment.status.ilike(term),
            )
        )
        .order_by(Payment.created_at.desc())
        .limit(limit)
    )
    for payment in (await session.execute(payment_stmt)).scalars().all():
        hits.append(
            SearchHit(
                kind="payment",
                ref=payment.payment_id,
                label=payment.provider_ref or payment.payment_id,
                status=payment.status,
                created_at=payment.created_at,
                quick_actions=_build_quick_actions("payment", payment.payment_id),
            )
        )

    hits.sort(key=lambda item: item.created_at, reverse=True)
    return hits[:limit]


def _normalize(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _conflicts(existing_start: datetime, existing_duration: int, candidate_start: datetime, candidate_duration: int) -> bool:
    buffer_delta = timedelta(minutes=BUFFER_MINUTES)
    existing_end = existing_start + timedelta(minutes=existing_duration)
    candidate_end = candidate_start + timedelta(minutes=candidate_duration)
    return candidate_start < existing_end + buffer_delta and candidate_end > existing_start - buffer_delta


async def _blocking_bookings(
    session: AsyncSession,
    team_id: int,
    window_start: datetime,
    window_end: datetime,
    *,
    exclude_booking_id: str | None = None,
) -> Iterable[Booking]:
    stmt = select(Booking).where(
        Booking.team_id == team_id,
        Booking.starts_at < window_end + timedelta(minutes=BUFFER_MINUTES),
        Booking.starts_at > window_start - timedelta(minutes=BUFFER_MINUTES),
        Booking.status.in_(BLOCKING_STATUSES),
    )
    if exclude_booking_id:
        stmt = stmt.where(Booking.booking_id != exclude_booking_id)
    result = await session.execute(stmt)
    return result.scalars().all()


async def _team_for_org(session: AsyncSession, org_id, team_id: int | None) -> Team:
    team = None
    if team_id:
        team = await session.get(Team, team_id)
    if team is None:
        team = await ensure_default_team(session)
    if getattr(team, "org_id", None) != org_id:
        raise PermissionError("Team does not belong to org")
    return team


async def list_schedule(
    session: AsyncSession, org_id, day: date, team_id: int | None = None
) -> dict[str, object]:
    team = await _team_for_org(session, org_id, team_id)
    day_start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    bookings_stmt = select(Booking).where(
        Booking.org_id == org_id,
        Booking.team_id == team.team_id,
        Booking.status.in_(BLOCKING_STATUSES),
        Booking.starts_at >= day_start,
        Booking.starts_at < day_end,
    ).order_by(Booking.starts_at.asc())
    bookings = (await session.execute(bookings_stmt)).scalars().all()

    blackout_stmt = select(TeamBlackout).where(
        TeamBlackout.team_id == team.team_id,
        TeamBlackout.starts_at < day_end,
        TeamBlackout.ends_at > day_start,
    )
    blackouts = (await session.execute(blackout_stmt)).scalars().all()

    slots = await generate_slots(day, DEFAULT_SLOT_DURATION_MINUTES, session, team_id=team.team_id)

    return {
        "team_id": team.team_id,
        "day": day,
        "bookings": [
            {
                "booking_id": b.booking_id,
                "starts_at": _normalize(b.starts_at),
                "duration_minutes": b.duration_minutes,
                "status": b.status,
            }
            for b in bookings
        ],
        "blackouts": [
            {
                "starts_at": _normalize(b.starts_at),
                "ends_at": _normalize(b.ends_at),
                "reason": b.reason,
            }
            for b in blackouts
        ],
        "available_slots": [_normalize(slot) for slot in slots],
    }


async def move_booking(
    session: AsyncSession,
    org_id,
    booking_id: str,
    starts_at: datetime,
    *,
    duration_minutes: int | None = None,
    team_id: int | None = None,
) -> Booking:
    booking = await session.get(Booking, booking_id)
    if booking is None:
        raise LookupError("booking_not_found")
    if booking.org_id != org_id:
        raise PermissionError("cross_org_forbidden")
    target_team = await _team_for_org(session, org_id, team_id or booking.team_id)

    duration = duration_minutes or booking.duration_minutes or DEFAULT_SLOT_DURATION_MINUTES
    normalized_start = _normalize(starts_at)
    normalized_end = normalized_start + timedelta(minutes=duration)

    for other in await _blocking_bookings(
        session, target_team.team_id, normalized_start, normalized_end, exclude_booking_id=booking.booking_id
    ):
        if _conflicts(_normalize(other.starts_at), other.duration_minutes, normalized_start, duration):
            raise ValueError("conflict_with_existing_booking")

    blackout_stmt = select(TeamBlackout).where(
        TeamBlackout.team_id == target_team.team_id,
        TeamBlackout.starts_at < normalized_end,
        TeamBlackout.ends_at > normalized_start,
    )
    blackout = (await session.execute(blackout_stmt)).scalar_one_or_none()
    if blackout:
        raise ValueError("conflict_with_blackout")

    booking.starts_at = normalized_start
    booking.duration_minutes = duration
    booking.team_id = target_team.team_id
    await session.commit()
    await session.refresh(booking)
    logger.info(
        "booking_moved",
        extra={
            "extra": {
                "booking_id": booking.booking_id,
                "starts_at": booking.starts_at.isoformat(),
                "team_id": booking.team_id,
                "duration_minutes": booking.duration_minutes,
            }
        },
    )
    return booking


async def block_team_slot(
    session: AsyncSession,
    org_id,
    *,
    team_id: int | None,
    starts_at: datetime,
    ends_at: datetime,
    reason: str | None = None,
) -> TeamBlackout:
    target_team = await _team_for_org(session, org_id, team_id)
    normalized_start = _normalize(starts_at)
    normalized_end = _normalize(ends_at)
    if normalized_end <= normalized_start:
        raise ValueError("invalid_window")

    for booking in await _blocking_bookings(
        session, target_team.team_id, normalized_start, normalized_end
    ):
        if _conflicts(_normalize(booking.starts_at), booking.duration_minutes, normalized_start, (normalized_end - normalized_start).seconds // 60):
            raise ValueError("conflict_with_existing_booking")

    overlap_stmt = select(TeamBlackout).where(
        TeamBlackout.team_id == target_team.team_id,
        TeamBlackout.starts_at < normalized_end,
        TeamBlackout.ends_at > normalized_start,
    )
    if (await session.execute(overlap_stmt)).scalar_one_or_none():
        raise ValueError("conflict_with_blackout")

    blackout = TeamBlackout(
        team_id=target_team.team_id,
        starts_at=normalized_start,
        ends_at=normalized_end,
        reason=reason,
    )
    session.add(blackout)
    await session.commit()
    await session.refresh(blackout)
    logger.info(
        "team_slot_blocked",
        extra={"extra": {"team_id": target_team.team_id, "starts_at": normalized_start.isoformat(), "ends_at": normalized_end.isoformat()}},
    )
    return blackout


async def bulk_update_bookings(
    session: AsyncSession,
    org_id,
    booking_ids: Iterable[str],
    *,
    team_id: int | None = None,
    status: str | None = None,
    send_reminder: bool = False,
    adapter=None,
) -> dict[str, int]:
    ids = list(booking_ids)
    if not ids:
        return {"updated": 0, "reminders_sent": 0}

    stmt = select(Booking, Lead).join(Lead, Lead.lead_id == Booking.lead_id, isouter=True).where(
        Booking.org_id == org_id, Booking.booking_id.in_(ids)
    )
    result = await session.execute(stmt)
    rows = result.all()
    updated = 0
    reminders = 0

    for booking, lead in rows:
        if team_id is not None:
            booking.team_id = team_id
        if status is not None:
            booking.status = status
        updated += 1
        if send_reminder and lead:
            delivered = await email_service.send_booking_reminder_email(session, adapter, booking, lead, dedupe=True)
            if delivered:
                reminders += 1

    await session.commit()
    return {"updated": updated, "reminders_sent": reminders}


async def list_templates() -> list[dict[str, str]]:
    return [
        {"template": email_service.EMAIL_TYPE_BOOKING_PENDING, "version": "v1"},
        {"template": email_service.EMAIL_TYPE_BOOKING_CONFIRMED, "version": "v1"},
        {"template": email_service.EMAIL_TYPE_BOOKING_REMINDER, "version": "v1"},
        {"template": email_service.EMAIL_TYPE_BOOKING_COMPLETED, "version": "v1"},
        {"template": email_service.EMAIL_TYPE_INVOICE_SENT, "version": "v1"},
        {"template": email_service.EMAIL_TYPE_INVOICE_OVERDUE, "version": "v1"},
    ]


async def render_template_preview(template: str, sample_booking: Booking | None, sample_lead: Lead | None, sample_invoice: Invoice | None) -> tuple[str, str]:
    render_map = {
        email_service.EMAIL_TYPE_BOOKING_PENDING: email_service._render_booking_pending,  # noqa: SLF001
        email_service.EMAIL_TYPE_BOOKING_CONFIRMED: email_service._render_booking_confirmed,  # noqa: SLF001
        email_service.EMAIL_TYPE_BOOKING_REMINDER: email_service._render_booking_reminder,  # noqa: SLF001
        email_service.EMAIL_TYPE_BOOKING_COMPLETED: email_service._render_booking_completed,  # noqa: SLF001
    }
    invoice_renders = {
        email_service.EMAIL_TYPE_INVOICE_SENT: email_service._render_invoice_sent,  # noqa: SLF001
        email_service.EMAIL_TYPE_INVOICE_OVERDUE: email_service._render_invoice_overdue,  # noqa: SLF001
    }
    if template in render_map:
        if not sample_booking or not sample_lead:
            raise ValueError("booking_and_lead_required")
        return render_map[template](sample_booking, sample_lead)
    if template in invoice_renders:
        if not sample_invoice or not sample_lead:
            raise ValueError("invoice_and_lead_required")
        public_link = None
        if template == email_service.EMAIL_TYPE_INVOICE_SENT:
            public_link = "https://example.invalid/invoice"
        return invoice_renders[template](sample_invoice, sample_lead, public_link)
    raise ValueError("template_not_supported")


async def resend_email_event(session: AsyncSession, adapter, org_id, event_id: str) -> dict[str, str]:
    stmt = select(EmailEvent).where(EmailEvent.event_id == event_id, EmailEvent.org_id == org_id)
    event = (await session.execute(stmt)).scalar_one_or_none()
    if event is None:
        raise LookupError("event_not_found")

    delivered = await email_service._try_send_email(  # noqa: SLF001
        adapter,
        event.recipient,
        event.subject,
        event.body,
        context={"email_type": event.email_type, "booking_id": event.booking_id, "invoice_id": event.invoice_id},
    )
    status = "delivered" if delivered else "queued"
    logger.info("email_event_resend", extra={"extra": {"event_id": event.event_id, "status": status}})
    return {"event_id": event.event_id, "status": status}

