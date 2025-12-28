import html
import json
import logging
import secrets
from datetime import date, datetime, time, timezone
from typing import Iterable, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_bot_store
from app.domain.analytics import schemas as analytics_schemas
from app.domain.analytics.service import (
    EventType,
    average_revenue_cents,
    conversion_counts,
    duration_accuracy,
    estimated_duration_from_booking,
    estimated_revenue_from_lead,
    log_event,
)
from app.dependencies import get_db_session
from app.domain.bookings.db_models import Booking
from app.domain.bookings import schemas as booking_schemas
from app.domain.bookings import service as booking_service
from app.domain.export_events.db_models import ExportEvent
from app.domain.export_events.schemas import ExportEventResponse
from app.domain.leads import statuses as lead_statuses
from app.domain.leads.db_models import Lead, ReferralCredit
from app.domain.leads.service import grant_referral_credit
from app.domain.leads.schemas import AdminLeadResponse, AdminLeadStatusUpdateRequest, admin_lead_from_model
from app.domain.leads.statuses import assert_valid_transition, is_valid_status
from app.domain.notifications import email_service
from app.domain.pricing.config_loader import load_pricing_config
from app.domain.retention import cleanup_retention
from app.infra.bot_store import BotStore
from app.settings import settings

router = APIRouter()
logger = logging.getLogger(__name__)
security = HTTPBasic(auto_error=False)


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _format_ts(value: float | None) -> str:
    if value is None:
        return "-"
    dt = datetime.fromtimestamp(value, tz=timezone.utc)
    return _format_dt(dt)


async def verify_admin_or_dispatcher(credentials: HTTPBasicCredentials | None = Depends(security)) -> str:
    admin_username = settings.admin_basic_username
    admin_password = settings.admin_basic_password
    dispatcher_username = settings.dispatcher_basic_username
    dispatcher_password = settings.dispatcher_basic_password

    if (not admin_username or not admin_password) and (not dispatcher_username or not dispatcher_password):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin access not configured",
        )

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication",
            headers={"WWW-Authenticate": "Basic"},
        )

    if admin_username and admin_password and secrets.compare_digest(credentials.username, admin_username) and secrets.compare_digest(credentials.password, admin_password):
        return "admin"
    if dispatcher_username and dispatcher_password and secrets.compare_digest(credentials.username, dispatcher_username) and secrets.compare_digest(credentials.password, dispatcher_password):
        return "dispatcher"

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication",
        headers={"WWW-Authenticate": "Basic"},
    )


async def require_admin(role: str = Depends(verify_admin_or_dispatcher)) -> str:
    if role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return role


def _filter_badge(filter_key: str, active_filters: set[str]) -> str:
    labels = {
        "needs_human": "Needs human",
        "waiting_for_contact": "Waiting for contact",
        "order_created": "Order created",
    }
    label = labels.get(filter_key, filter_key)
    is_active = filter_key in active_filters
    href = "" if is_active else f"?filters={filter_key}"
    class_name = "badge" + (" badge-active" if is_active else "")
    return f'<a class="{class_name}" href="{href}">{html.escape(label)}</a>'


def _render_filters(active_filters: set[str]) -> str:
    parts = [
        '<div class="filters">',
        "<strong>Quick filters:</strong>",
        _filter_badge("needs_human", active_filters),
        _filter_badge("waiting_for_contact", active_filters),
        _filter_badge("order_created", active_filters),
        '<a class="badge" href="/v1/admin/observability">Clear</a>',
        "</div>",
    ]
    return "".join(parts)


def _render_section(title: str, body: str) -> str:
    return f"<section><h2>{html.escape(title)}</h2>{body}</section>"


def _render_empty(message: str) -> str:
    return f"<p class=\"muted\">{html.escape(message)}</p>"


def _render_leads(leads: Iterable[Lead], active_filters: set[str]) -> str:
    cards: list[str] = []
    for lead in leads:
        tags: set[str] = set()
        bookings_count = len(getattr(lead, "bookings", []))
        if lead.status == lead_statuses.LEAD_STATUS_NEW:
            tags.add("waiting_for_contact")
        if bookings_count:
            tags.add("order_created")

        if active_filters and not active_filters.intersection(tags):
            continue

        contact_bits = [html.escape(lead.phone)]
        if lead.email:
            contact_bits.append(html.escape(lead.email))
        contact = " · ".join(contact_bits)
        tag_text = " ".join(f"<span class=\"tag\">{t}</span>" for t in sorted(tags))
        cards.append(
            """
            <div class="card">
              <div class="card-row">
                <div>
                  <div class="title">{name}</div>
                  <div class="muted">{contact}</div>
                </div>
                <div class="status">{status}</div>
              </div>
              <div class="card-row">
                <div class="muted">Created {created}</div>
                <div>{tags}</div>
              </div>
              <div class="muted">Notes: {notes}</div>
            </div>
            """.format(
                name=html.escape(lead.name),
                contact=contact,
                status=html.escape(lead.status),
                created=_format_dt(lead.created_at),
                notes=html.escape(lead.notes or "-"),
                tags=tag_text,
            )
        )
    if not cards:
        return _render_empty("No leads match the current filter.")
    return "".join(cards)


def _render_cases(cases: Iterable[object], active_filters: set[str]) -> str:
    cards: list[str] = []
    for case in cases:
        tags = {"needs_human"}
        if active_filters and not active_filters.intersection(tags):
            continue
        summary = getattr(case, "summary", "Escalated case") or "Escalated case"
        reason = getattr(case, "reason", "-")
        conversation_id = getattr(case, "source_conversation_id", None)
        cards.append(
            """
            <div class="card">
              <div class="card-row">
                <div>
                  <div class="title">{summary}</div>
                  <div class="muted">Reason: {reason}</div>
                </div>
                <div class="muted">{created}</div>
              </div>
              <div class="card-row">
                <a class="btn" href="/v1/admin/observability/cases/{case_id}">View detail</a>
                <div class="muted">Conversation: {conversation}</div>
              </div>
            </div>
            """.format(
                summary=html.escape(summary),
                reason=html.escape(reason),
                created=_format_ts(getattr(case, "created_at", None)),
                case_id=html.escape(getattr(case, "case_id", "")),
                conversation=html.escape(conversation_id or "n/a"),
            )
        )
    if not cards:
        return _render_empty("No cases match the current filter.")
    return "".join(cards)


def _render_dialogs(
    conversations: Iterable[object],
    message_lookup: dict[str, list[object]],
    active_filters: set[str],
) -> str:
    cards: list[str] = []
    for conversation in conversations:
        tags: set[str] = set()
        status = getattr(conversation, "status", "")
        if str(status).lower() == "handed_off":
            tags.add("needs_human")

        if active_filters and not active_filters.intersection(tags):
            continue

        messages = message_lookup.get(conversation.conversation_id, [])
        last_message = messages[-1].text if messages else "No messages yet"
        cards.append(
            """
            <div class="card">
              <div class="card-row">
                <div class="title">{conversation_id}</div>
                <div class="status">{status}</div>
              </div>
              <div class="muted">Last message: {last_message}</div>
              <div class="muted">Updated {updated_at}</div>
            </div>
            """.format(
                conversation_id=html.escape(conversation.conversation_id),
                status=html.escape(str(status)),
                last_message=html.escape(last_message),
                updated_at=_format_ts(getattr(conversation, "updated_at", None)),
            )
        )
    if not cards:
        return _render_empty("No dialogs match the current filter.")
    return "".join(cards)


def _wrap_page(content: str) -> str:
    return f"""
    <html>
      <head>
        <title>Admin Observability</title>
        <style>
          body {{ font-family: Arial, sans-serif; margin: 24px; background: #fafafa; }}
          h1 {{ margin-bottom: 8px; }}
          h2 {{ margin-top: 28px; margin-bottom: 12px; }}
          .card {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px; margin-bottom: 10px; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }}
          .card-row {{ display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 6px; }}
          .title {{ font-weight: 600; }}
          .status {{ font-weight: 600; color: #2563eb; }}
          .muted {{ color: #6b7280; font-size: 13px; }}
          .filters {{ display: flex; gap: 8px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; }}
          .badge {{ padding: 4px 8px; border-radius: 999px; border: 1px solid #d1d5db; text-decoration: none; color: #111827; font-size: 13px; }}
          .badge-active {{ background: #2563eb; color: #fff; border-color: #2563eb; }}
          .btn {{ padding: 6px 10px; background: #111827; color: #fff; border-radius: 6px; text-decoration: none; font-size: 13px; }}
          .tag {{ display: inline-block; background: #eef2ff; color: #4338ca; padding: 2px 6px; border-radius: 6px; font-size: 12px; margin-left: 4px; }}
        </style>
      </head>
      <body>
        <h1>Admin — Leads, Cases & Dialogs</h1>
        {content}
      </body>
    </html>
    """


@router.get("/v1/admin/leads", response_model=List[AdminLeadResponse])
async def list_leads(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
    role: str = Depends(verify_admin_or_dispatcher),
) -> List[AdminLeadResponse]:
    stmt = (
        select(Lead)
        .options(
            selectinload(Lead.referral_credits),
            selectinload(Lead.referred_credit),
        )
        .order_by(Lead.created_at.desc())
        .limit(limit)
    )
    if status_filter and hasattr(Lead, "status"):
        normalized = status_filter.upper()
        if not is_valid_status(normalized):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid lead status filter: {status_filter}",
            )
        stmt = stmt.where(Lead.status == normalized)
    result = await session.execute(stmt)
    leads = result.scalars().all()
    return [admin_lead_from_model(lead) for lead in leads]


@router.post("/v1/admin/leads/{lead_id}/status", response_model=AdminLeadResponse)
async def update_lead_status(
    lead_id: str,
    request: AdminLeadStatusUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
    role: str = Depends(verify_admin_or_dispatcher),
) -> AdminLeadResponse:
    lead = await session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    try:
        assert_valid_transition(lead.status, request.status)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    lead.status = request.status
    await session.commit()
    await session.refresh(lead)
    credit_count = await session.scalar(
        select(func.count()).select_from(ReferralCredit).where(ReferralCredit.referrer_lead_id == lead.lead_id)
    )
    return admin_lead_from_model(lead, referral_credit_count=int(credit_count or 0))


@router.post("/v1/admin/email-scan", status_code=status.HTTP_202_ACCEPTED)
async def email_scan(
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
    role: str = Depends(verify_admin_or_dispatcher),
) -> dict[str, int]:
    adapter = getattr(http_request.app.state, "email_adapter", None)
    result = await email_service.scan_and_send_reminders(session, adapter)
    return result


@router.post("/v1/admin/bookings/{booking_id}/resend-last-email", status_code=status.HTTP_202_ACCEPTED)
async def resend_last_email(
    booking_id: str,
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
    role: str = Depends(verify_admin_or_dispatcher),
) -> dict[str, str]:
    booking = await session.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

    adapter = getattr(http_request.app.state, "email_adapter", None)
    try:
        return await email_service.resend_last_email(session, adapter, booking_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No prior email for booking") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Email send failed") from exc


@router.get("/v1/admin/export-dead-letter", response_model=List[ExportEventResponse])
async def list_export_dead_letter(
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),
    role: str = Depends(verify_admin_or_dispatcher),
) -> List[ExportEventResponse]:
    result = await session.execute(
        select(ExportEvent).order_by(ExportEvent.created_at.desc()).limit(limit)
    )
    events = result.scalars().all()
    return [
        ExportEventResponse(
            event_id=event.event_id,
            lead_id=event.lead_id,
            mode=event.mode,
            target_url_host=event.target_url_host,
            attempts=event.attempts,
            last_error_code=event.last_error_code,
            created_at=event.created_at,
        )
        for event in events
    ]


@router.post("/v1/admin/retention/cleanup")
async def run_retention_cleanup(
    session: AsyncSession = Depends(get_db_session),
    role: str = Depends(require_admin),
) -> dict[str, int]:
    return await cleanup_retention(session)


def _normalize_range(
    start: datetime | None, end: datetime | None
) -> tuple[datetime, datetime]:
    now = datetime.now(tz=timezone.utc)
    normalized_start = start or datetime.fromtimestamp(0, tz=timezone.utc)
    normalized_end = end or now
    if normalized_start.tzinfo is None:
        normalized_start = normalized_start.replace(tzinfo=timezone.utc)
    else:
        normalized_start = normalized_start.astimezone(timezone.utc)
    if normalized_end.tzinfo is None:
        normalized_end = normalized_end.replace(tzinfo=timezone.utc)
    else:
        normalized_end = normalized_end.astimezone(timezone.utc)
    if normalized_end < normalized_start:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid date range")
    return normalized_start, normalized_end


def _csv_line(key: str, value: object) -> str:
    return f"{key},{value if value is not None else ''}"


@router.get("/v1/admin/metrics", response_model=analytics_schemas.AdminMetricsResponse)
async def get_admin_metrics(
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    format: str | None = Query(default=None, pattern="^(json|csv)$"),
    session: AsyncSession = Depends(get_db_session),
    role: str = Depends(require_admin),
):
    start, end = _normalize_range(from_ts, to_ts)
    conversions = await conversion_counts(session, start, end)
    avg_revenue = await average_revenue_cents(session, start, end)
    avg_estimated, avg_actual, avg_delta, sample_size = await duration_accuracy(session, start, end)

    response_body = analytics_schemas.AdminMetricsResponse(
        range_start=start,
        range_end=end,
        conversions=analytics_schemas.ConversionMetrics(
            lead_created=conversions.get(EventType.lead_created, 0),
            booking_created=conversions.get(EventType.booking_created, 0),
            booking_confirmed=conversions.get(EventType.booking_confirmed, 0),
            job_completed=conversions.get(EventType.job_completed, 0),
        ),
        revenue=analytics_schemas.RevenueMetrics(
            average_estimated_revenue_cents=avg_revenue,
        ),
        accuracy=analytics_schemas.DurationAccuracy(
            sample_size=sample_size,
            average_estimated_duration_minutes=avg_estimated,
            average_actual_duration_minutes=avg_actual,
            average_delta_minutes=avg_delta,
        ),
    )

    if format == "csv":
        lines = [
            _csv_line("range_start", response_body.range_start.isoformat()),
            _csv_line("range_end", response_body.range_end.isoformat()),
            _csv_line("lead_created", response_body.conversions.lead_created),
            _csv_line("booking_created", response_body.conversions.booking_created),
            _csv_line("booking_confirmed", response_body.conversions.booking_confirmed),
            _csv_line("job_completed", response_body.conversions.job_completed),
            _csv_line("average_estimated_revenue_cents", response_body.revenue.average_estimated_revenue_cents),
            _csv_line("average_estimated_duration_minutes", response_body.accuracy.average_estimated_duration_minutes),
            _csv_line("average_actual_duration_minutes", response_body.accuracy.average_actual_duration_minutes),
            _csv_line("average_delta_minutes", response_body.accuracy.average_delta_minutes),
            _csv_line("accuracy_sample_size", response_body.accuracy.sample_size),
        ]
        return Response("\n".join(lines), media_type="text/csv")

    return response_body


@router.post("/v1/admin/pricing/reload", status_code=status.HTTP_202_ACCEPTED)
async def reload_pricing(role: str = Depends(require_admin)) -> dict[str, str]:
    load_pricing_config(settings.pricing_config_path)
    return {"status": "reloaded"}


@router.get("/v1/admin/bookings", response_model=list[booking_schemas.AdminBookingListItem])
async def list_bookings(
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    status_filter: str | None = Query(default=None, alias="status"),
    session: AsyncSession = Depends(get_db_session),
    role: str = Depends(verify_admin_or_dispatcher),
):
    today = datetime.now(tz=booking_service.LOCAL_TZ).date()
    start_date = from_date or today
    end_date = to_date or start_date
    start_dt = datetime.combine(start_date, time.min, tzinfo=booking_service.LOCAL_TZ).astimezone(timezone.utc)
    end_dt = datetime.combine(end_date, time.max, tzinfo=booking_service.LOCAL_TZ).astimezone(timezone.utc)
    if end_dt < start_dt:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid date range")

    stmt = select(Booking, Lead).outerjoin(Lead, Lead.lead_id == Booking.lead_id).where(
        Booking.starts_at >= start_dt,
        Booking.starts_at <= end_dt,
    )
    if status_filter:
        stmt = stmt.where(Booking.status == status_filter.upper())
    stmt = stmt.order_by(Booking.starts_at.asc())
    result = await session.execute(stmt)
    return [
        booking_schemas.AdminBookingListItem(
            booking_id=booking.booking_id,
            lead_id=booking.lead_id,
            starts_at=booking.starts_at,
            duration_minutes=booking.duration_minutes,
            status=booking.status,
            lead_name=lead.name if lead else None,
            lead_email=lead.email if lead else None,
        )
        for booking, lead in result.all()
    ]


@router.post("/v1/admin/bookings/{booking_id}/confirm", response_model=booking_schemas.BookingResponse)
async def confirm_booking(
    booking_id: str,
    session: AsyncSession = Depends(get_db_session),
    role: str = Depends(verify_admin_or_dispatcher),
):
    booking = await session.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

    try:
        booking_service.assert_valid_booking_transition(booking.status, "CONFIRMED")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    lead = await session.get(Lead, booking.lead_id) if booking.lead_id else None
    if booking.status != "CONFIRMED":
        booking.status = "CONFIRMED"
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

    return booking_schemas.BookingResponse(
        booking_id=booking.booking_id,
        status=booking.status,
        starts_at=booking.starts_at,
        duration_minutes=booking.duration_minutes,
        actual_duration_minutes=booking.actual_duration_minutes,
        deposit_required=booking.deposit_required,
        deposit_cents=booking.deposit_cents,
        deposit_policy=booking.deposit_policy,
        deposit_status=booking.deposit_status,
        checkout_url=None,
    )


@router.get("/v1/admin/observability", response_class=HTMLResponse)
async def admin_observability(
    filters: list[str] = Query(default=[]),
    session: AsyncSession = Depends(get_db_session),
    store: BotStore = Depends(get_bot_store),
    role: str = Depends(verify_admin_or_dispatcher),
) -> HTMLResponse:
    active_filters = {value.lower() for value in filters if value}
    lead_stmt = (
        select(Lead)
        .options(selectinload(Lead.bookings))
        .order_by(Lead.created_at.desc())
        .limit(200)
    )
    leads = (await session.execute(lead_stmt)).scalars().all()
    cases = sorted(await store.list_cases(), key=lambda c: getattr(c, "created_at", 0), reverse=True)
    conversations = sorted(
        await store.list_conversations(), key=lambda c: getattr(c, "updated_at", 0), reverse=True
    )
    message_lookup: dict[str, list[object]] = {}
    for conversation in conversations:
        message_lookup[conversation.conversation_id] = await store.list_messages(conversation.conversation_id)

    content = "".join(
        [
            _render_filters(active_filters),
            _render_section("Cases", _render_cases(cases, active_filters)),
            _render_section("Leads", _render_leads(leads, active_filters)),
            _render_section("Dialogs", _render_dialogs(conversations, message_lookup, active_filters)),
        ]
    )
    return HTMLResponse(_wrap_page(content))


@router.get("/v1/admin/observability/cases/{case_id}", response_class=HTMLResponse)
async def admin_case_detail(
    case_id: str,
    store: BotStore = Depends(get_bot_store),
    role: str = Depends(verify_admin_or_dispatcher),
) -> HTMLResponse:
    cases = await store.list_cases()
    case = next((item for item in cases if getattr(item, "case_id", None) == case_id), None)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    payload = getattr(case, "payload", {}) or {}
    contact_fields = {}
    conversation_data = payload.get("conversation") or {}
    state_data = conversation_data.get("state") or {}
    if isinstance(state_data, dict):
        contact_fields = state_data.get("filled_fields") or {}
        if not isinstance(contact_fields, dict):
            contact_fields = {}
    contact_data = contact_fields.get("contact") if isinstance(contact_fields, dict) else None
    contact_data = contact_data if isinstance(contact_data, dict) else {}
    phone = contact_fields.get("phone") or contact_data.get("phone")
    email = contact_fields.get("email") or contact_data.get("email")

    transcript = payload.get("messages") or []
    if not transcript and getattr(case, "source_conversation_id", None):
        messages = await store.list_messages(case.source_conversation_id)
        transcript = [
            {
                "role": msg.role,
                "text": msg.text,
                "ts": msg.created_at,
            }
            for msg in messages
        ]

    transcript_html = "".join(
        """
        <div class="card">
          <div class="card-row">
            <div class="title">{role}</div>
            <div class="muted">{ts}</div>
          </div>
          <div>{text}</div>
        </div>
        """.format(
            role=html.escape(str(message.get("role", "") if isinstance(message, dict) else getattr(message, "role", ""))),
            ts=_format_ts(
                message.get("ts") if isinstance(message, dict) else getattr(message, "ts", getattr(message, "created_at", None))
            ),
            text=html.escape(
                str(message.get("text", "")) if isinstance(message, dict) else str(getattr(message, "text", ""))
            ),
        )
        for message in transcript
    )
    if not transcript_html:
        transcript_html = _render_empty("No transcript available")

    quick_actions: list[str] = []
    contact_quick_actions = {
        "phone": phone,
        "email": email,
    }
    for field, value in contact_quick_actions.items():
        if value:
            quick_actions.append(
                """
                <button class="btn" onclick="navigator.clipboard.writeText({value})">Copy {label}</button>
                """.format(value=json.dumps(str(value)), label=field.title())
            )
    quick_actions.append("<button class=\"btn\" onclick=\"alert('Mark contacted placeholder')\">Mark contacted</button>")

    summary_block = """
        <div class="card">
          <div class="card-row">
            <div>
              <div class="title">{summary}</div>
              <div class="muted">Reason: {reason}</div>
            </div>
            <div class="muted">{created}</div>
          </div>
          <div class="card-row">
            <div class="muted">Conversation: {conversation}</div>
            <div class="muted">Case ID: {case_id}</div>
          </div>
          <div class="card-row">{actions}</div>
        </div>
    """.format(
        summary=html.escape(getattr(case, "summary", "Escalated case") or "Escalated case"),
        reason=html.escape(getattr(case, "reason", "-")),
        created=_format_ts(getattr(case, "created_at", None)),
        conversation=html.escape(getattr(case, "source_conversation_id", "")),
        case_id=html.escape(getattr(case, "case_id", "")),
        actions="".join(quick_actions),
    )

    content = "".join(
        [
            summary_block,
            _render_section("Transcript", transcript_html),
        ]
    )
    return HTMLResponse(_wrap_page(content))


@router.post("/v1/admin/bookings/{booking_id}/cancel", response_model=booking_schemas.BookingResponse)
async def cancel_booking(
    booking_id: str,
    session: AsyncSession = Depends(get_db_session),
    role: str = Depends(verify_admin_or_dispatcher),
):
    booking = await session.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

    try:
        booking_service.assert_valid_booking_transition(booking.status, "CANCELLED")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    booking.status = "CANCELLED"
    await session.commit()
    await session.refresh(booking)
    return booking_schemas.BookingResponse(
        booking_id=booking.booking_id,
        status=booking.status,
        starts_at=booking.starts_at,
        duration_minutes=booking.duration_minutes,
        actual_duration_minutes=booking.actual_duration_minutes,
        deposit_required=booking.deposit_required,
        deposit_cents=booking.deposit_cents,
        deposit_policy=booking.deposit_policy,
        deposit_status=booking.deposit_status,
        checkout_url=None,
    )


@router.post("/v1/admin/bookings/{booking_id}/reschedule", response_model=booking_schemas.BookingResponse)
async def reschedule_booking(
    booking_id: str,
    request: booking_schemas.BookingRescheduleRequest,
    session: AsyncSession = Depends(get_db_session),
    role: str = Depends(verify_admin_or_dispatcher),
):
    booking = await session.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
    if booking.status in {"DONE", "CANCELLED"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Booking is no longer active")

    try:
        booking = await booking_service.reschedule_booking(
            session,
            booking,
            request.starts_at,
            request.duration_minutes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return booking_schemas.BookingResponse(
        booking_id=booking.booking_id,
        status=booking.status,
        starts_at=booking.starts_at,
        duration_minutes=booking.duration_minutes,
        actual_duration_minutes=booking.actual_duration_minutes,
        deposit_required=booking.deposit_required,
        deposit_cents=booking.deposit_cents,
        deposit_policy=booking.deposit_policy,
        deposit_status=booking.deposit_status,
        checkout_url=None,
    )


@router.post(
    "/v1/admin/bookings/{booking_id}/complete",
    response_model=booking_schemas.BookingResponse,
)
async def complete_booking(
    booking_id: str,
    request: booking_schemas.BookingCompletionRequest,
    session: AsyncSession = Depends(get_db_session),
    role: str = Depends(verify_admin_or_dispatcher),
):
    try:
        booking = await booking_service.mark_booking_completed(
            session, booking_id, request.actual_duration_minutes
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if booking is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

    return booking_schemas.BookingResponse(
        booking_id=booking.booking_id,
        status=booking.status,
        starts_at=booking.starts_at,
        duration_minutes=booking.duration_minutes,
        actual_duration_minutes=booking.actual_duration_minutes,
        deposit_required=booking.deposit_required,
        deposit_cents=booking.deposit_cents,
        deposit_policy=booking.deposit_policy,
        deposit_status=booking.deposit_status,
        checkout_url=None,
    )
