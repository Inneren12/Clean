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
from app.domain.workers.db_models import Worker
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
    relevance_score: int = 0  # Higher = more relevant


def _build_quick_actions(kind: str, ref: str, extra_context: dict | None = None) -> list[QuickAction]:
    """Build context-aware quick actions for search results."""
    if kind == "lead":
        actions = [QuickAction(label="View lead", target=f"/v1/admin/leads/{ref}")]
        if extra_context and extra_context.get("email"):
            actions.append(QuickAction(label="Email", target=f"mailto:{extra_context['email']}"))
        if extra_context and extra_context.get("phone"):
            actions.append(QuickAction(label="Call", target=f"tel:{extra_context['phone']}"))
        return actions
    if kind == "booking":
        return [
            QuickAction(label="View booking", target=f"/v1/bookings/{ref}"),
            QuickAction(label="Move", target=f"/v1/admin/schedule/{ref}/move", method="POST"),
            QuickAction(label="Timeline", target=f"/v1/admin/timeline/booking/{ref}"),
        ]
    if kind == "invoice":
        return [
            QuickAction(label="View invoice", target=f"/v1/invoices/{ref}"),
            QuickAction(label="Resend", target=f"/v1/admin/invoices/{ref}/resend", method="POST"),
            QuickAction(label="Timeline", target=f"/v1/admin/timeline/invoice/{ref}"),
        ]
    if kind == "payment":
        return [QuickAction(label="Review payment", target=f"/v1/admin/payments/{ref}")]
    if kind == "worker":
        actions = [QuickAction(label="View worker", target=f"/v1/admin/workers/{ref}")]
        if extra_context and extra_context.get("phone"):
            actions.append(QuickAction(label="Call", target=f"tel:{extra_context['phone']}"))
        return actions
    return []


def _calculate_relevance(q: str, text_fields: list[str | None]) -> int:
    """Calculate relevance score (higher = more relevant).

    Exact matches score highest, then prefix matches, then contains.
    """
    q_lower = q.lower().strip()
    score = 0

    for field in text_fields:
        if not field:
            continue
        field_lower = field.lower()

        if field_lower == q_lower:
            score += 100  # Exact match
        elif field_lower.startswith(q_lower):
            score += 50  # Prefix match
        elif q_lower in field_lower:
            score += 10  # Contains

    return score


async def global_search(session: AsyncSession, org_id, q: str, limit: int = 20) -> list[SearchHit]:
    """Enhanced global search with weighted results and expanded coverage.

    Searches across leads, bookings, invoices, payments, and workers.
    Results are sorted by relevance score (exact/prefix/contains matches) then recency.
    """
    if not q:
        return []

    term = f"%{q.strip()}%"
    hits: list[SearchHit] = []

    # Search leads
    lead_stmt: Select = (
        select(Lead)
        .where(Lead.org_id == org_id)
        .where(or_(Lead.name.ilike(term), Lead.email.ilike(term), Lead.phone.ilike(term)))
        .order_by(Lead.created_at.desc())
        .limit(limit * 2)  # Fetch more for relevance sorting
    )
    for lead in (await session.execute(lead_stmt)).scalars().all():
        relevance = _calculate_relevance(q, [lead.name, lead.email, lead.phone])
        hits.append(
            SearchHit(
                kind="lead",
                ref=lead.lead_id,
                label=lead.name,
                status=lead.status,
                created_at=lead.created_at,
                quick_actions=_build_quick_actions("lead", lead.lead_id, {"email": lead.email, "phone": lead.phone}),
                relevance_score=relevance,
            )
        )

    # Search bookings
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
        .limit(limit * 2)
    )
    for booking in (await session.execute(booking_stmt)).scalars().all():
        relevance = _calculate_relevance(q, [booking.booking_id, booking.status])
        hits.append(
            SearchHit(
                kind="booking",
                ref=booking.booking_id,
                label=f"Booking {booking.booking_id}",
                status=booking.status,
                created_at=booking.created_at,
                quick_actions=_build_quick_actions("booking", booking.booking_id),
                relevance_score=relevance,
            )
        )

    # Search invoices
    invoice_stmt: Select = (
        select(Invoice)
        .where(Invoice.org_id == org_id)
        .where(or_(Invoice.invoice_number.ilike(term), Invoice.invoice_id.ilike(term)))
        .order_by(Invoice.created_at.desc())
        .limit(limit * 2)
    )
    for invoice in (await session.execute(invoice_stmt)).scalars().all():
        relevance = _calculate_relevance(q, [invoice.invoice_number, invoice.invoice_id])
        hits.append(
            SearchHit(
                kind="invoice",
                ref=invoice.invoice_id,
                label=invoice.invoice_number,
                status=invoice.status,
                created_at=invoice.created_at,
                quick_actions=_build_quick_actions("invoice", invoice.invoice_id),
                relevance_score=relevance,
            )
        )

    # Search payments
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
        .limit(limit * 2)
    )
    for payment in (await session.execute(payment_stmt)).scalars().all():
        relevance = _calculate_relevance(q, [payment.payment_id, payment.provider_ref, payment.status])
        hits.append(
            SearchHit(
                kind="payment",
                ref=payment.payment_id,
                label=payment.provider_ref or payment.payment_id,
                status=payment.status,
                created_at=payment.created_at,
                quick_actions=_build_quick_actions("payment", payment.payment_id),
                relevance_score=relevance,
            )
        )

    # Search workers
    worker_stmt: Select = (
        select(Worker)
        .where(Worker.org_id == org_id)
        .where(
            or_(
                Worker.name.ilike(term),
                Worker.phone.ilike(term),
                Worker.email.ilike(term),
            )
        )
        .order_by(Worker.created_at.desc())
        .limit(limit * 2)
    )
    for worker in (await session.execute(worker_stmt)).scalars().all():
        relevance = _calculate_relevance(q, [worker.name, worker.phone, worker.email])
        hits.append(
            SearchHit(
                kind="worker",
                ref=str(worker.worker_id),
                label=worker.name,
                status="active" if worker.is_active else "inactive",
                created_at=worker.created_at,
                quick_actions=_build_quick_actions("worker", str(worker.worker_id), {"phone": worker.phone}),
                relevance_score=relevance,
            )
        )

    # Sort by relevance (desc) then created_at (desc)
    hits.sort(key=lambda item: (item.relevance_score, item.created_at), reverse=True)
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
        team = await ensure_default_team(session, org_id)
    if getattr(team, "org_id", None) != org_id:
        raise PermissionError("Team does not belong to org")
    return team


async def _team_conflicts(
    session: AsyncSession,
    team: Team,
    window_start: datetime,
    window_end: datetime,
    *,
    exclude_booking_id: str | None = None,
) -> list[dict[str, object]]:
    conflicts: list[dict[str, object]] = []
    for booking in await _blocking_bookings(
        session, team.team_id, window_start, window_end, exclude_booking_id=exclude_booking_id
    ):
        if _conflicts(
            _normalize(booking.starts_at), booking.duration_minutes, window_start, (window_end - window_start).seconds // 60
        ):
            conflicts.append(
                {
                    "kind": "booking",
                    "reference": booking.booking_id,
                    "starts_at": _normalize(booking.starts_at),
                    "ends_at": _normalize(booking.starts_at) + timedelta(minutes=booking.duration_minutes),
                    "note": "existing booking",
                }
            )

    blackout_stmt = select(TeamBlackout).where(
        TeamBlackout.team_id == team.team_id,
        TeamBlackout.starts_at < window_end,
        TeamBlackout.ends_at > window_start,
    )
    for blackout in (await session.execute(blackout_stmt)).scalars().all():
        conflicts.append(
            {
                "kind": "blackout",
                "reference": str(blackout.id),
                "starts_at": _normalize(blackout.starts_at),
                "ends_at": _normalize(blackout.ends_at),
                "note": blackout.reason,
            }
        )

    return conflicts


async def _worker_conflicts(
    session: AsyncSession,
    worker: Worker,
    window_start: datetime,
    window_end: datetime,
    *,
    exclude_booking_id: str | None = None,
) -> list[dict[str, object]]:
    conflicts: list[dict[str, object]] = []
    stmt = select(Booking).where(
        Booking.org_id == worker.org_id,
        Booking.assigned_worker_id == worker.worker_id,
        Booking.starts_at < window_end + timedelta(minutes=BUFFER_MINUTES),
        Booking.starts_at > window_start - timedelta(minutes=BUFFER_MINUTES),
        Booking.status.in_(BLOCKING_STATUSES),
    )
    if exclude_booking_id:
        stmt = stmt.where(Booking.booking_id != exclude_booking_id)

    for booking in (await session.execute(stmt)).scalars().all():
        if _conflicts(
            _normalize(booking.starts_at), booking.duration_minutes, window_start, (window_end - window_start).seconds // 60
        ):
            conflicts.append(
                {
                    "kind": "worker_booking",
                    "reference": booking.booking_id,
                    "starts_at": _normalize(booking.starts_at),
                    "ends_at": _normalize(booking.starts_at) + timedelta(minutes=booking.duration_minutes),
                    "note": "worker has a conflicting booking",
                }
            )
    return conflicts


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


async def suggest_schedule_resources(
    session: AsyncSession,
    org_id,
    *,
    starts_at: datetime,
    ends_at: datetime,
    skill_tags: list[str] | None = None,
    exclude_booking_id: str | None = None,
) -> dict[str, list[dict[str, object]]]:
    normalized_start = _normalize(starts_at)
    normalized_end = _normalize(ends_at)
    if normalized_end <= normalized_start:
        raise ValueError("invalid_window")

    stmt = select(Team).where(Team.org_id == org_id).order_by(Team.team_id)
    teams = (await session.execute(stmt)).scalars().all()
    if not teams:
        teams = [await ensure_default_team(session, org_id)]

    skill_terms = [tag.lower().strip() for tag in (skill_tags or []) if tag]
    available_teams: list[dict[str, object]] = []
    team_conflicts: dict[int, list[dict[str, object]]] = {}

    for team in teams:
        conflicts = await _team_conflicts(
            session, team, normalized_start, normalized_end, exclude_booking_id=exclude_booking_id
        )
        team_conflicts[team.team_id] = conflicts
        if not conflicts:
            available_teams.append({"team_id": team.team_id, "name": team.name})

    worker_stmt: Select = (
        select(Worker, Team.name)
        .join(Team, Worker.team_id == Team.team_id)
        .where(Team.org_id == org_id)
        .where(Worker.is_active.is_(True))
        .order_by(Worker.worker_id)
    )
    available_workers: list[dict[str, object]] = []
    for worker, team_name in (await session.execute(worker_stmt)).all():
        role_text = (worker.role or "").lower()
        if skill_terms and not all(term in role_text for term in skill_terms):
            continue

        team_conflict = team_conflicts.get(worker.team_id, [])
        worker_conflict = await _worker_conflicts(
            session, worker, normalized_start, normalized_end, exclude_booking_id=exclude_booking_id
        )
        if team_conflict or worker_conflict:
            continue

        available_workers.append(
            {
                "worker_id": worker.worker_id,
                "name": worker.name,
                "team_id": worker.team_id,
                "team_name": team_name,
            }
        )

    return {"teams": available_teams, "workers": available_workers}


async def check_schedule_conflicts(
    session: AsyncSession,
    org_id,
    *,
    starts_at: datetime,
    ends_at: datetime,
    team_id: int | None = None,
    booking_id: str | None = None,
    worker_id: int | None = None,
) -> list[dict[str, object]]:
    normalized_start = _normalize(starts_at)
    normalized_end = _normalize(ends_at)
    if normalized_end <= normalized_start:
        raise ValueError("invalid_window")

    team = await _team_for_org(session, org_id, team_id)
    conflicts = await _team_conflicts(
        session, team, normalized_start, normalized_end, exclude_booking_id=booking_id
    )

    if worker_id is not None:
        worker = await session.get(Worker, worker_id)
        if worker is None:
            raise LookupError("worker_not_found")
        if worker.org_id != org_id:
            raise PermissionError("Cross-org worker access blocked")
        worker_conflicts = await _worker_conflicts(
            session, worker, normalized_start, normalized_end, exclude_booking_id=booking_id
        )
        conflicts.extend(worker_conflicts)

    return conflicts


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

