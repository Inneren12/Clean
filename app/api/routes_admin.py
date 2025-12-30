import html
import json
import csv
import io
import logging
import math
from datetime import date, datetime, time, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, List, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_auth import (
    AdminIdentity,
    AdminPermission,
    ROLE_PERMISSIONS,
    require_admin,
    require_dispatch,
    require_finance,
    require_viewer,
    verify_admin_or_dispatcher,
)
from app.dependencies import get_bot_store
from app.domain.addons import schemas as addon_schemas
from app.domain.addons import service as addon_service
from app.domain.addons.db_models import AddonDefinition
from app.domain.analytics import schemas as analytics_schemas
from app.domain.analytics.service import (
    EventType,
    average_revenue_cents,
    conversion_counts,
    duration_accuracy,
    kpi_aggregates,
    estimated_duration_from_booking,
    estimated_revenue_from_lead,
    log_event,
)
from app.dependencies import get_db_session
from app.domain.bookings.db_models import Booking, Team
from app.domain.bookings import schemas as booking_schemas
from app.domain.bookings import service as booking_service
from app.domain.export_events.db_models import ExportEvent
from app.domain.export_events.schemas import ExportEventResponse
from app.domain.invoices import schemas as invoice_schemas
from app.domain.invoices import service as invoice_service
from app.domain.invoices import statuses as invoice_statuses
from app.domain.invoices.db_models import Invoice, Payment
from app.domain.leads import statuses as lead_statuses
from app.domain.leads.db_models import Lead, ReferralCredit
from app.domain.nps.db_models import SupportTicket
from app.domain.leads.service import grant_referral_credit
from app.domain.leads.schemas import AdminLeadResponse, AdminLeadStatusUpdateRequest, admin_lead_from_model
from app.domain.leads.statuses import assert_valid_transition, is_valid_status
from app.domain.notifications import email_service
from app.domain.nps import schemas as nps_schemas, service as nps_service
from app.domain.pricing.config_loader import load_pricing_config
from app.domain.reason_logs import schemas as reason_schemas
from app.domain.reason_logs import service as reason_service
from app.domain.retention import cleanup_retention
from app.domain.subscriptions import schemas as subscription_schemas
from app.domain.subscriptions import service as subscription_service
from app.domain.subscriptions.db_models import Subscription
from app.domain.admin_audit import service as audit_service
from app.domain.workers.db_models import Worker
from app.infra.csrf import get_csrf_token, issue_csrf_token, render_csrf_input, require_csrf
from app.infra.bot_store import BotStore
from app.infra.i18n import render_lang_toggle, resolve_lang, tr
from app.settings import settings

router = APIRouter(dependencies=[Depends(require_viewer)])
logger = logging.getLogger(__name__)


class AdminProfileResponse(BaseModel):
    username: str
    role: str
    permissions: list[str]


@router.get("/v1/admin/profile", response_model=AdminProfileResponse)
async def get_admin_profile(identity: AdminIdentity = Depends(require_viewer)) -> AdminProfileResponse:
    return AdminProfileResponse(
        username=identity.username,
        role=getattr(identity.role, "value", str(identity.role)),
        permissions=sorted(
            getattr(permission, "value", str(permission))
            for permission in ROLE_PERMISSIONS.get(identity.role, set())
        ),
    )


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _format_ts(value: float | None) -> str:
    if value is None:
        return "-"
    dt = datetime.fromtimestamp(value, tz=timezone.utc)
    return _format_dt(dt)


def _icon(name: str) -> str:
    icons = {
        "eye": """
        <svg class=\"icon\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\">
          <path d=\"M1.5 12s4-6.5 10.5-6.5S22.5 12 22.5 12s-4 6.5-10.5 6.5S1.5 12 1.5 12Z\" />
          <circle cx=\"12\" cy=\"12\" r=\"3.5\" />
        </svg>
        """,
        "receipt": """
        <svg class=\"icon\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\">
          <path d=\"M7 3.5 9 2l2 1.5L13 2l2 1.5L17 2l2 1.5V21l-2-1.5-2 1.5-2-1.5-2 1.5-2-1.5-2 1.5V3.5Z\" />
          <path d=\"M8.5 8.5h7M8.5 12h7M8.5 15.5h4\" />
        </svg>
        """,
        "search": """
        <svg class=\"icon\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\">
          <circle cx=\"11\" cy=\"11\" r=\"6.5\" />
          <path d=\"m16 16 4.5 4.5\" />
        </svg>
        """,
        "copy": """
        <svg class=\"icon\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\">
          <rect x=\"8\" y=\"8\" width=\"11\" height=\"11\" rx=\"2.5\" />
          <path d=\"M5 15.5V6.5A2.5 2.5 0 0 1 7.5 4H15\" />
        </svg>
        """,
        "warning": """
        <svg class=\"icon\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"> 
          <path d=\"m12 3.5 9 16H3l9-16Z\" />
          <path d=\"M12 10v4.5M12 18.1h.01\" />
        </svg>
        """,
        "users": """
        <svg class=\"icon\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\">
          <path d=\"M17 21v-2.5A3.5 3.5 0 0 0 13.5 15h-7A3.5 3.5 0 0 0 3 18.5V21\" />
          <circle cx=\"10\" cy=\"7\" r=\"3.5\" />
          <path d=\"M21 21v-2a3 3 0 0 0-2.5-3 3.1 3.1 0 0 0-1.3 0\" />
          <path d=\"M17.5 3.5a3 3 0 1 1-2 5.3\" />
        </svg>
        """,
        "calendar": """
        <svg class=\"icon\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\">
          <rect x=\"3.5\" y=\"5\" width=\"17\" height=\"15.5\" rx=\"2.5\" />
          <path d=\"M7.5 3.5v3M16.5 3.5v3M3.5 9.5h17\" />
        </svg>
        """,
        "edit": """
        <svg class=\"icon\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\">
          <path d=\"M4 20h4l11-11-4-4L4 16v4Z\" />
          <path d=\"M14 5 19 10\" />
        </svg>
        """,
        "plus": """
        <svg class=\"icon\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\">
          <path d=\"M12 5v14M5 12h14\" />
        </svg>
        """,
    }
    return icons.get(name, "")


def _filter_badge(filter_key: str, active_filters: set[str], lang: str | None) -> str:
    labels = {
        "needs_human": tr(lang, "admin.filters.needs_human"),
        "waiting_for_contact": tr(lang, "admin.filters.waiting_for_contact"),
        "order_created": tr(lang, "admin.filters.order_created"),
    }
    label = labels.get(filter_key, filter_key)
    is_active = filter_key in active_filters
    href = "" if is_active else f"?filters={filter_key}"
    class_name = "badge" + (" badge-active" if is_active else "")
    return f'<a class="{class_name}" href="{href}">{html.escape(label)}</a>'


def _render_filters(active_filters: set[str], lang: str | None) -> str:
    parts = [
        '<div class="filters">',
        f"<div class=\"with-icon\">{_icon('search')}<strong>{tr(lang, 'admin.filters.title')}</strong></div>",
        _filter_badge("needs_human", active_filters, lang),
        _filter_badge("waiting_for_contact", active_filters, lang),
        _filter_badge("order_created", active_filters, lang),
        f'<a class="badge" href="/v1/admin/observability">{tr(lang, "admin.filters.clear")}</a>',
        "</div>",
    ]
    return "".join(parts)


def _render_section(title: str, body: str) -> str:
    return f"<section><h2>{html.escape(title)}</h2>{body}</section>"


def _render_empty(message: str) -> str:
    return f"<p class=\"muted\">{html.escape(message)}</p>"


def _render_leads(leads: Iterable[Lead], active_filters: set[str], lang: str | None) -> str:
    cards: list[str] = []
    tag_labels = {
        "needs_human": tr(lang, "admin.filters.needs_human"),
        "waiting_for_contact": tr(lang, "admin.filters.waiting_for_contact"),
        "order_created": tr(lang, "admin.filters.order_created"),
    }
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
        tag_text = " ".join(
            f"<span class=\"tag\">{html.escape(tag_labels.get(t, t))}</span>" for t in sorted(tags)
        )
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
                <div class="muted">{created}</div>
                <div>{tags}</div>
              </div>
              <div class="muted">{notes}</div>
            </div>
            """.format(
                name=html.escape(lead.name),
                contact=contact,
                status=html.escape(lead.status),
                created=html.escape(tr(lang, "admin.leads.created_at", created=_format_dt(lead.created_at))),
                notes=html.escape(tr(lang, "admin.leads.notes", notes=lead.notes or "-")),
                tags=tag_text,
            )
        )
    if not cards:
        return _render_empty(tr(lang, "admin.empty.leads"))
    return "".join(cards)


def _render_cases(cases: Iterable[object], active_filters: set[str], lang: str | None) -> str:
    cards: list[str] = []
    for case in cases:
        tags = {"needs_human"}
        if active_filters and not active_filters.intersection(tags):
            continue
        summary = getattr(case, "summary", tr(lang, "admin.cases.default_summary")) or tr(
            lang, "admin.cases.default_summary"
        )
        reason = getattr(case, "reason", "-")
        conversation_id = getattr(case, "source_conversation_id", None)
        cards.append(
            """
            <div class="card">
              <div class="card-row">
                <div>
                  <div class="title">{summary}</div>
                  <div class="muted">{reason_label} {reason}</div>
                </div>
                <div class="muted">{created}</div>
              </div>
              <div class="card-row">
                <a class="btn" href="/v1/admin/observability/cases/{case_id}">{view_detail}</a>
                <div class="muted">{conversation_label}: {conversation}</div>
              </div>
            </div>
            """.format(
                summary=html.escape(summary),
                reason_label=html.escape(tr(lang, "admin.labels.reason")),
                reason=html.escape(reason),
                created=html.escape(
                    tr(lang, "admin.cases.created_at", created=_format_ts(getattr(case, "created_at", None)))
                ),
                case_id=html.escape(getattr(case, "case_id", "")),
                view_detail=html.escape(tr(lang, "admin.cases.view_detail")),
                conversation_label=html.escape(tr(lang, "admin.labels.conversation")),
                conversation=html.escape(conversation_id or "n/a"),
            )
        )
    if not cards:
        return _render_empty(tr(lang, "admin.empty.cases"))
    return "".join(cards)


def _render_dialogs(
    conversations: Iterable[object],
    message_lookup: dict[str, list[object]],
    active_filters: set[str],
    lang: str | None,
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
        last_message = messages[-1].text if messages else tr(lang, "admin.dialogs.no_messages")
        cards.append(
            """
            <div class="card">
              <div class="card-row">
                <div class="title">{conversation_id}</div>
                <div class="status">{status}</div>
              </div>
              <div class="muted">{last_message_label}: {last_message}</div>
              <div class="muted">{updated_label} {updated_at}</div>
            </div>
            """.format(
                conversation_id=html.escape(conversation.conversation_id),
                status=html.escape(str(status)),
                last_message=html.escape(last_message),
                last_message_label=html.escape(tr(lang, "admin.dialogs.last_message")),
                updated_label=html.escape(tr(lang, "admin.dialogs.updated")),
                updated_at=html.escape(_format_ts(getattr(conversation, "updated_at", None))),
            )
        )
    if not cards:
        return _render_empty(tr(lang, "admin.empty.dialogs"))
    return "".join(cards)


def _wrap_page(
    request: Request,
    content: str,
    *,
    title: str = "Admin",
    active: str | None = None,
    page_lang: str | None = None,
) -> str:
    resolved_lang = resolve_lang(request)
    page_lang = page_lang or resolved_lang
    nav_lang = "en" if active == "invoices" else resolved_lang
    nav_links = [
        (
            _icon("eye") + html.escape(tr(nav_lang, "admin.nav.observability")),
            "/v1/admin/observability",
            "observability",
        ),
        (
            _icon("users") + html.escape(tr(nav_lang, "admin.nav.workers")),
            "/v1/admin/ui/workers",
            "workers",
        ),
        (
            _icon("calendar") + html.escape(tr(nav_lang, "admin.nav.dispatch")),
            "/v1/admin/ui/dispatch",
            "dispatch",
        ),
        (
            _icon("receipt") + html.escape(tr(nav_lang, "admin.nav.invoices")),
            "/v1/admin/ui/invoices",
            "invoices",
        ),
    ]
    nav = "".join(
        f'<a class="nav-link{" nav-link-active" if active == key else ""}" href="{href}"><span class="with-icon">{label}</span></a>'
        for label, href, key in nav_links
    )
    lang_toggle = render_lang_toggle(request, resolved_lang)
    return f"""
    <html lang=\"{html.escape(page_lang)}\">
      <head>
        <title>{html.escape(title)}</title>
        <style>
          body {{ font-family: Arial, sans-serif; margin: 0; background: #f8fafc; color: #111827; }}
          h1 {{ margin: 0 0 8px; font-size: 24px; }}
          h2 {{ margin: 24px 0 12px; font-size: 18px; }}
          a {{ color: #2563eb; }}
          .page {{ max-width: 1080px; margin: 0 auto; padding: 24px; }}
          .topbar {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 24px; gap: 16px; flex-wrap: wrap; }}
          .topbar-actions {{ display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }}
          .nav {{ display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }}
          .nav-link {{ text-decoration: none; color: #374151; padding: 8px 12px; border-radius: 10px; border: 1px solid transparent; background: #fff; box-shadow: 0 1px 2px rgba(0,0,0,0.04); }}
          .nav-link-active {{ background: #111827; color: #fff; border-color: #111827; box-shadow: 0 1px 2px rgba(0,0,0,0.08); }}
          .lang-toggle {{ display: flex; gap: 8px; font-size: 13px; align-items: center; }}
          .lang-link {{ text-decoration: none; color: #374151; padding: 6px 10px; border-radius: 8px; border: 1px solid transparent; font-weight: 600; background: #fff; }}
          .lang-link-active {{ background: #111827; color: #fff; border-color: #111827; box-shadow: 0 1px 2px rgba(0,0,0,0.08); }}
          .card {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px; margin-bottom: 16px; box-shadow: 0 10px 15px -10px rgba(15,23,42,0.15); }}
          .card-row {{ display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 8px; flex-wrap: wrap; }}
          .title {{ font-weight: 600; }}
          .status {{ font-weight: 600; color: #2563eb; }}
          .muted {{ color: #6b7280; font-size: 13px; }}
          .small {{ font-size: 12px; }}
          .filters {{ display: flex; gap: 8px; align-items: flex-end; margin-bottom: 16px; flex-wrap: wrap; }}
          .form-group {{ display: flex; flex-direction: column; gap: 6px; font-size: 13px; }}
          .input {{ padding: 8px 10px; border-radius: 8px; border: 1px solid #d1d5db; min-width: 160px; font-size: 14px; background: #fff; }}
          .badge {{ display: inline-flex; align-items: center; gap: 6px; padding: 6px 10px; border-radius: 999px; border: 1px solid #d1d5db; text-decoration: none; color: #111827; font-size: 13px; background: #fff; }}
          .badge-active {{ background: #2563eb; color: #fff; border-color: #2563eb; }}
          .badge-status {{ font-weight: 600; }}
          .status-draft {{ background: #f3f4f6; }}
          .status-sent {{ background: #eef2ff; color: #4338ca; border-color: #c7d2fe; }}
          .status-partial {{ background: #fffbeb; color: #92400e; border-color: #fcd34d; }}
          .status-paid {{ background: #ecfdf3; color: #065f46; border-color: #a7f3d0; }}
          .status-overdue {{ background: #fef2f2; color: #b91c1c; border-color: #fecaca; }}
          .status-void {{ background: #f3f4f6; color: #374151; }}
          .btn {{ padding: 10px 14px; background: #111827; color: #fff; border-radius: 8px; text-decoration: none; font-size: 13px; border: none; cursor: pointer; display: inline-flex; align-items: center; gap: 8px; }}
          .btn.secondary {{ background: #fff; color: #111827; border: 1px solid #d1d5db; }}
          .btn.small {{ padding: 8px 10px; font-size: 12px; }}
          .btn:disabled {{ opacity: 0.6; cursor: not-allowed; }}
          .tag {{ display: inline-block; background: #eef2ff; color: #4338ca; padding: 4px 8px; border-radius: 8px; font-size: 12px; margin-left: 4px; }}
          .table {{ width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 14px; }}
          .table th, .table td {{ padding: 12px 10px; border-bottom: 1px solid #e5e7eb; text-align: left; vertical-align: top; }}
          .table th {{ background: #f9fafb; font-weight: 600; }}
          .table .muted {{ font-size: 12px; }}
          .table .align-right {{ text-align: right; }}
          .pill {{ display: inline-flex; align-items: center; gap: 6px; padding: 8px 12px; border-radius: 10px; border: 1px solid #e5e7eb; background: #f9fafb; }}
          .metric-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 16px; margin-top: 12px; }}
          .metric {{ background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 10px; padding: 12px; }}
          .metric .label {{ font-size: 12px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.03em; }}
          .metric .value {{ font-size: 18px; font-weight: 700; margin-top: 2px; }}
          .danger {{ color: #b91c1c; }}
          .success {{ color: #065f46; }}
          .chip {{ display: inline-flex; align-items: center; gap: 8px; background: #eef2ff; border: 1px solid #c7d2fe; padding: 8px 10px; border-radius: 10px; font-size: 13px; }}
          .stack {{ display: flex; flex-direction: column; gap: 8px; }}
          .row-highlight {{ background: #fffbeb; }}
          .actions {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
          .section {{ margin-top: 16px; }}
          .note {{ padding: 10px 12px; background: #f9fafb; border: 1px dashed #d1d5db; border-radius: 10px; }}
          .with-icon {{ display: inline-flex; align-items: center; gap: 8px; }}
          .icon {{ width: 18px; height: 18px; display: block; }}
        </style>
      </head>
      <body>
        <div class="page">
          <div class="topbar">
            <h1>{html.escape(title)}</h1>
            <div class="topbar-actions">
              <div class="nav">{nav}</div>
              <div class="lang-toggle">{lang_toggle}</div>
            </div>
          </div>
          {content}
        </div>
      </body>
    </html>
    """


@router.get("/v1/admin/leads", response_model=List[AdminLeadResponse])
async def list_leads(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
    _identity: AdminIdentity = Depends(require_viewer),
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
    http_request: Request,
    lead_id: str,
    payload: AdminLeadStatusUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
    identity: AdminIdentity = Depends(require_dispatch),
) -> AdminLeadResponse:
    lead = await session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    before = admin_lead_from_model(lead).model_dump(mode="json")

    try:
        assert_valid_transition(lead.status, payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    lead.status = payload.status
    credit_count = await session.scalar(
        select(func.count()).select_from(ReferralCredit).where(ReferralCredit.referrer_lead_id == lead.lead_id)
    )
    response_body = admin_lead_from_model(lead, referral_credit_count=int(credit_count or 0))

    http_request.state.explicit_admin_audit = True
    await audit_service.record_action(
        session,
        identity=identity,
        action="lead_status_update",
        resource_type="lead",
        resource_id=lead.lead_id,
        before=before,
        after=response_body.model_dump(mode="json"),
    )
    await session.commit()
    return response_body


@router.post("/v1/admin/email-scan", status_code=status.HTTP_202_ACCEPTED)
async def email_scan(
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
    _identity: AdminIdentity = Depends(require_dispatch),
) -> dict[str, int]:
    adapter = getattr(http_request.app.state, "email_adapter", None)
    result = await email_service.scan_and_send_reminders(session, adapter)
    return result


@router.post("/v1/admin/bookings/{booking_id}/resend-last-email", status_code=status.HTTP_202_ACCEPTED)
async def resend_last_email(
    booking_id: str,
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
    _identity: AdminIdentity = Depends(require_dispatch),
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
    _principal: AdminIdentity = Depends(require_viewer),
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
    _admin: AdminIdentity = Depends(require_admin),
) -> dict[str, int]:
    return await cleanup_retention(session)


def _admin_subscription_response(model: Subscription) -> subscription_schemas.AdminSubscriptionListItem:
    return subscription_schemas.AdminSubscriptionListItem(
        subscription_id=model.subscription_id,
        client_id=model.client_id,
        status=model.status,
        frequency=model.frequency,
        next_run_at=model.next_run_at,
        base_service_type=model.base_service_type,
        base_price=model.base_price,
        created_at=model.created_at,
    )


@router.get(
    "/v1/admin/subscriptions",
    response_model=list[subscription_schemas.AdminSubscriptionListItem],
)
async def list_subscriptions_admin(
    identity: AdminIdentity = Depends(require_admin), session: AsyncSession = Depends(get_db_session)
) -> list[subscription_schemas.AdminSubscriptionListItem]:
    stmt = select(Subscription).order_by(Subscription.created_at.desc())
    result = await session.execute(stmt)
    subscriptions = result.scalars().all()
    return [_admin_subscription_response(sub) for sub in subscriptions]


@router.post(
    "/v1/admin/subscriptions/run",
    response_model=subscription_schemas.SubscriptionRunResult,
)
async def run_subscriptions(
    http_request: Request,
    identity: AdminIdentity = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> subscription_schemas.SubscriptionRunResult:
    adapter = getattr(http_request.app.state, "email_adapter", None)
    result = await subscription_service.generate_due_orders(session, email_adapter=adapter)
    await session.commit()
    return subscription_schemas.SubscriptionRunResult(
        processed=result.processed, created_orders=result.created_orders
    )


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


def _normalize_date_range(start: date | None, end: date | None) -> tuple[date, date]:
    range_start = start or date(1970, 1, 1)
    range_end = end or date.today()
    if range_end < range_start:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid date range")
    return range_start, range_end


def _csv_line(key: str, value: object) -> str:
    return f"{key},{value if value is not None else ''}"


@router.get("/v1/admin/metrics", response_model=analytics_schemas.AdminMetricsResponse)
async def get_admin_metrics(
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    format: str | None = Query(default=None, pattern="^(json|csv)$"),
    session: AsyncSession = Depends(get_db_session),
    _admin: AdminIdentity = Depends(require_admin),
):
    start, end = _normalize_range(from_ts, to_ts)
    if not settings.metrics_enabled:
        return analytics_schemas.AdminMetricsResponse(
            range_start=start,
            range_end=end,
            conversions=analytics_schemas.ConversionMetrics(
                lead_created=0,
                booking_created=0,
                booking_confirmed=0,
                job_completed=0,
            ),
            revenue=analytics_schemas.RevenueMetrics(average_estimated_revenue_cents=None),
            accuracy=analytics_schemas.DurationAccuracy(
                sample_size=0,
                average_delta_minutes=None,
                average_actual_duration_minutes=None,
                average_estimated_duration_minutes=None,
            ),
            financial=analytics_schemas.FinancialKpis(
                total_revenue_cents=0,
                revenue_per_day_cents=0.0,
                margin_cents=0,
                average_order_value_cents=None,
            ),
            operational=analytics_schemas.OperationalKpis(
                crew_utilization=None,
                cancellation_rate=0.0,
                retention_30_day=0.0,
                retention_60_day=0.0,
                retention_90_day=0.0,
            ),
        )
    conversions = await conversion_counts(session, start, end)
    avg_revenue = await average_revenue_cents(session, start, end)
    avg_estimated, avg_actual, avg_delta, sample_size = await duration_accuracy(session, start, end)
    kpis = await kpi_aggregates(session, start, end)

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
        financial=analytics_schemas.FinancialKpis(**kpis["financial"]),
        operational=analytics_schemas.OperationalKpis(**kpis["operational"]),
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
            _csv_line("total_revenue_cents", response_body.financial.total_revenue_cents),
            _csv_line("revenue_per_day_cents", response_body.financial.revenue_per_day_cents),
            _csv_line("margin_cents", response_body.financial.margin_cents),
            _csv_line("average_order_value_cents", response_body.financial.average_order_value_cents),
            _csv_line("crew_utilization", response_body.operational.crew_utilization),
            _csv_line("cancellation_rate", response_body.operational.cancellation_rate),
            _csv_line("retention_30_day", response_body.operational.retention_30_day),
            _csv_line("retention_60_day", response_body.operational.retention_60_day),
            _csv_line("retention_90_day", response_body.operational.retention_90_day),
        ]
        return Response("\n".join(lines), media_type="text/csv")

    return response_body


@router.get("/v1/admin/reports/gst", response_model=invoice_schemas.GstReportResponse)
async def admin_gst_report(
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    session: AsyncSession = Depends(get_db_session),
    _identity: AdminIdentity = Depends(require_finance),
) -> invoice_schemas.GstReportResponse:
    start, end = _normalize_date_range(from_date, to_date)
    stmt = (
        select(
            func.count(Invoice.invoice_id),
            func.coalesce(func.sum(Invoice.subtotal_cents), 0),
            func.coalesce(func.sum(Invoice.tax_cents), 0),
        )
        .where(
            Invoice.issue_date >= start,
            Invoice.issue_date <= end,
            Invoice.status.notin_(
                [invoice_statuses.INVOICE_STATUS_VOID, invoice_statuses.INVOICE_STATUS_DRAFT]
            ),
        )
        .select_from(Invoice)
    )
    result = await session.execute(stmt)
    invoice_count, subtotal_cents, tax_cents = result.one()
    return invoice_schemas.GstReportResponse(
        range_start=start,
        range_end=end,
        invoice_count=int(invoice_count or 0),
        taxable_subtotal_cents=int(subtotal_cents or 0),
        tax_cents=int(tax_cents or 0),
    )


@router.get("/v1/admin/exports/sales-ledger.csv")
async def export_sales_ledger(
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    session: AsyncSession = Depends(get_db_session),
    _identity: AdminIdentity = Depends(require_finance),
) -> Response:
    start, end = _normalize_date_range(from_date, to_date)
    stmt = (
        select(Invoice)
        .options(selectinload(Invoice.payments))
        .where(
            Invoice.issue_date >= start,
            Invoice.issue_date <= end,
            Invoice.status.notin_(
                [invoice_statuses.INVOICE_STATUS_VOID, invoice_statuses.INVOICE_STATUS_DRAFT]
            ),
        )
        .order_by(Invoice.issue_date.asc(), Invoice.invoice_number.asc())
    )
    result = await session.execute(stmt)
    invoices = result.scalars().all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "invoice_number",
            "issue_date",
            "due_date",
            "status",
            "currency",
            "subtotal_cents",
            "tax_cents",
            "total_cents",
            "paid_cents",
            "balance_due_cents",
            "customer_id",
            "booking_id",
        ]
    )
    for invoice in invoices:
        paid_cents = sum(
            payment.amount_cents
            for payment in invoice.payments
            if payment.status == invoice_statuses.PAYMENT_STATUS_SUCCEEDED
        )
        balance_due_cents = max(invoice.total_cents - paid_cents, 0)
        writer.writerow(
            [
                invoice.invoice_number,
                invoice.issue_date.isoformat(),
                invoice.due_date.isoformat() if invoice.due_date else "",
                invoice.status,
                invoice.currency,
                invoice.subtotal_cents,
                invoice.tax_cents,
                invoice.total_cents,
                paid_cents,
                balance_due_cents,
                invoice.customer_id or "",
                invoice.order_id or "",
            ]
        )

    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=sales-ledger.csv"},
    )


@router.get("/v1/admin/exports/payments.csv")
async def export_payments_ledger(
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    session: AsyncSession = Depends(get_db_session),
    _identity: AdminIdentity = Depends(require_finance),
) -> Response:
    start, end = _normalize_date_range(from_date, to_date)
    start_dt = datetime.combine(start, time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(end, time.max, tzinfo=timezone.utc)
    timestamp_expr = func.coalesce(Payment.received_at, Payment.created_at).label("ts")
    stmt = (
        select(Payment, Invoice.invoice_number, Invoice.order_id, timestamp_expr)
        .join(Invoice, Payment.invoice_id == Invoice.invoice_id, isouter=True)
        .where(
            timestamp_expr >= start_dt,
            timestamp_expr <= end_dt,
            Payment.status == invoice_statuses.PAYMENT_STATUS_SUCCEEDED,
            Payment.invoice_id.isnot(None),
        )
        .order_by(timestamp_expr.asc(), Payment.created_at.asc(), Payment.payment_id.asc())
    )
    result = await session.execute(stmt)
    rows = result.all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "payment_id",
            "invoice_number",
            "booking_id",
            "provider",
            "method",
            "status",
            "amount_cents",
            "currency",
            "received_at",
            "created_at",
        ]
    )

    for payment, invoice_number, invoice_order_id, _payment_ts in rows:
        writer.writerow(
            [
                payment.payment_id,
                invoice_number or "",
                payment.booking_id or invoice_order_id or "",
                payment.provider,
                payment.method,
                payment.status,
                payment.amount_cents,
                payment.currency,
                payment.received_at.isoformat() if payment.received_at else "",
                payment.created_at.isoformat(),
            ]
        )

    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=payments.csv"},
    )


@router.get("/v1/admin/exports/deposits.csv")
async def export_deposits_ledger(
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    session: AsyncSession = Depends(get_db_session),
    _identity: AdminIdentity = Depends(require_finance),
) -> Response:
    start, end = _normalize_date_range(from_date, to_date)
    start_dt = datetime.combine(start, time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(end, time.max, tzinfo=timezone.utc)
    timestamp_expr = func.coalesce(Payment.received_at, Payment.created_at).label("ts")
    stmt = (
        select(Payment, timestamp_expr)
        .where(
            timestamp_expr >= start_dt,
            timestamp_expr <= end_dt,
            Payment.status == invoice_statuses.PAYMENT_STATUS_SUCCEEDED,
            Payment.invoice_id.is_(None),
            Payment.booking_id.isnot(None),
        )
        .order_by(timestamp_expr.asc(), Payment.created_at.asc(), Payment.payment_id.asc())
    )
    result = await session.execute(stmt)
    rows = result.all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "payment_id",
        "booking_id",
        "provider",
        "method",
        "amount_cents",
        "currency",
        "received_at",
        "created_at",
    ])

    for payment, _payment_ts in rows:
        writer.writerow(
            [
                payment.payment_id,
                payment.booking_id or "",
                payment.provider,
                payment.method,
                payment.amount_cents,
                payment.currency,
                payment.received_at.isoformat() if payment.received_at else "",
                payment.created_at.isoformat(),
            ]
        )

    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=deposits.csv"},
    )


def _resolve_worker_rate(worker: Worker | None) -> int:
    if worker and worker.hourly_rate_cents is not None:
        return worker.hourly_rate_cents
    return settings.default_worker_hourly_rate_cents


def _actual_minutes(booking: Booking) -> int:
    if booking.actual_seconds is not None:
        return int(math.ceil(booking.actual_seconds / 60))
    if booking.actual_duration_minutes is not None:
        return booking.actual_duration_minutes
    return booking.duration_minutes


@router.get("/v1/admin/reports/pnl", response_model=invoice_schemas.PnlReportResponse)
async def admin_pnl_report(
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    session: AsyncSession = Depends(get_db_session),
    _identity: AdminIdentity = Depends(require_finance),
) -> invoice_schemas.PnlReportResponse:
    start, end = _normalize_date_range(from_date, to_date)
    stmt = (
        select(Invoice, Booking, Worker)
        .join(Booking, Invoice.order_id == Booking.booking_id)
        .join(Worker, Booking.assigned_worker_id == Worker.worker_id, isouter=True)
        .where(
            Invoice.issue_date >= start,
            Invoice.issue_date <= end,
            Invoice.status.notin_(
                [invoice_statuses.INVOICE_STATUS_VOID, invoice_statuses.INVOICE_STATUS_DRAFT]
            ),
            Booking.status.in_(["DONE", "CONFIRMED"]),
        )
        .order_by(Invoice.issue_date.asc(), Invoice.invoice_number.asc())
    )
    result = await session.execute(stmt)
    rows: list[invoice_schemas.PnlRow] = []
    for invoice, booking, worker in result.all():
        worker_rate_cents = _resolve_worker_rate(worker)
        minutes = _actual_minutes(booking)
        labour_cents = int(
            (Decimal(worker_rate_cents) * Decimal(minutes) / Decimal(60))
            .quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
        payment_fees_cents = 0
        revenue_cents = invoice.subtotal_cents
        margin_cents = revenue_cents - labour_cents - payment_fees_cents
        margin_pct = round((margin_cents / revenue_cents) * 100, 4) if revenue_cents else None

        rows.append(
            invoice_schemas.PnlRow(
                booking_id=booking.booking_id,
                invoice_number=invoice.invoice_number,
                revenue_cents=revenue_cents,
                labour_cents=labour_cents,
                payment_fees_cents=payment_fees_cents,
                margin_cents=margin_cents,
                margin_pct=margin_pct,
                worker_rate_cents=worker_rate_cents,
                actual_minutes=minutes,
            )
        )

    return invoice_schemas.PnlReportResponse(range_start=start, range_end=end, rows=rows)


@router.post("/v1/admin/pricing/reload", status_code=status.HTTP_202_ACCEPTED)
async def reload_pricing(_admin: AdminIdentity = Depends(require_admin)) -> dict[str, str]:
    load_pricing_config(settings.pricing_config_path)
    return {"status": "reloaded"}


@router.get("/v1/admin/bookings", response_model=list[booking_schemas.AdminBookingListItem])
async def list_bookings(
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    status_filter: str | None = Query(default=None, alias="status"),
    session: AsyncSession = Depends(get_db_session),
    _identity: AdminIdentity = Depends(require_viewer),
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
    http_request: Request,
    booking_id: str,
    session: AsyncSession = Depends(get_db_session),
    identity: AdminIdentity = Depends(require_dispatch),
):
    booking = await session.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

    before_state = booking_schemas.BookingResponse(
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
        risk_score=booking.risk_score,
        risk_band=booking.risk_band,
        risk_reasons=booking.risk_reasons,
        cancellation_exception=booking.cancellation_exception,
        cancellation_exception_note=booking.cancellation_exception_note,
    ).model_dump(mode="json")

    try:
        booking_service.assert_valid_booking_transition(booking.status, "CONFIRMED")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if booking.deposit_required and booking.deposit_status != "paid":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Deposit required before confirmation"
        )

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
    response_body = booking_schemas.BookingResponse(
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
        risk_score=booking.risk_score,
        risk_band=booking.risk_band,
        risk_reasons=booking.risk_reasons,
        cancellation_exception=booking.cancellation_exception,
        cancellation_exception_note=booking.cancellation_exception_note,
    )
    http_request.state.explicit_admin_audit = True
    await audit_service.record_action(
        session,
        identity=identity,
        action="booking_confirm",
        resource_type="booking",
        resource_id=booking.booking_id,
        before=before_state,
        after=response_body.model_dump(mode="json"),
    )
    await session.commit()
    return response_body


@router.get("/v1/admin/observability", response_class=HTMLResponse)
async def admin_observability(
    request: Request,
    filters: list[str] = Query(default=[]),
    session: AsyncSession = Depends(get_db_session),
    store: BotStore = Depends(get_bot_store),
    _identity: AdminIdentity = Depends(require_viewer),
) -> HTMLResponse:
    lang = resolve_lang(request)
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
            _render_filters(active_filters, lang),
            _render_section(tr(lang, "admin.sections.cases"), _render_cases(cases, active_filters, lang)),
            _render_section(tr(lang, "admin.sections.leads"), _render_leads(leads, active_filters, lang)),
            _render_section(
                tr(lang, "admin.sections.dialogs"),
                _render_dialogs(conversations, message_lookup, active_filters, lang),
            ),
        ]
    )
    return HTMLResponse(
        _wrap_page(
            request,
            content,
            title=tr(lang, "admin.observability.title"),
            active="observability",
            page_lang=lang,
        )
    )


@router.get("/v1/admin/observability/cases/{case_id}", response_class=HTMLResponse)
async def admin_case_detail(
    case_id: str,
    request: Request,
    store: BotStore = Depends(get_bot_store),
    _identity: AdminIdentity = Depends(require_viewer),
) -> HTMLResponse:
    lang = resolve_lang(request)
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
        transcript_html = _render_empty(tr(lang, "admin.empty.transcript"))

    quick_actions: list[str] = []
    contact_quick_actions = {
        "phone": phone,
        "email": email,
    }
    for field, value in contact_quick_actions.items():
        if value:
            escaped_value = html.escape(str(value), quote=True)
            contact_label = tr(lang, f"admin.contact.{field}")
            if contact_label == f"admin.contact.{field}":
                contact_label = field.title()
            quick_actions.append(
                """
                <button class="btn" data-copy="{value}" onclick="navigator.clipboard.writeText(this.dataset.copy)">{copy_label} {label}</button>
                """.format(
                    value=escaped_value,
                    copy_label=html.escape(tr(lang, "admin.buttons.copy")),
                    label=html.escape(contact_label),
                )
            )
    quick_actions.append(
        f"<button class=\"btn\" onclick=\"alert('Mark contacted placeholder')\">{html.escape(tr(lang, 'admin.buttons.mark_contacted'))}</button>"
    )

    summary_block = """
        <div class="card">
          <div class="card-row">
            <div>
              <div class="title">{summary}</div>
              <div class="muted">{reason_label} {reason}</div>
            </div>
            <div class="muted">{created}</div>
          </div>
          <div class="card-row">
            <div class="muted">{conversation_label}: {conversation}</div>
            <div class="muted">{case_label}: {case_id}</div>
          </div>
          <div class="card-row">{actions}</div>
        </div>
    """.format(
        summary=html.escape(getattr(case, "summary", "Escalated case") or "Escalated case"),
        reason_label=html.escape(tr(lang, "admin.labels.reason")),
        reason=html.escape(getattr(case, "reason", "-")),
        created=_format_ts(getattr(case, "created_at", None)),
        conversation_label=html.escape(tr(lang, "admin.labels.conversation")),
        conversation=html.escape(getattr(case, "source_conversation_id", "")),
        case_label=html.escape(tr(lang, "admin.labels.case_id")),
        case_id=html.escape(getattr(case, "case_id", "")),
        actions="".join(quick_actions),
    )

    content = "".join(
        [
            summary_block,
            _render_section(tr(lang, "admin.sections.transcript"), transcript_html),
        ]
    )
    return HTMLResponse(
        _wrap_page(
            request,
            content,
            title=tr(lang, "admin.observability.title"),
            active="observability",
            page_lang=lang,
        )
    )


@router.post("/v1/admin/bookings/{booking_id}/cancel", response_model=booking_schemas.BookingResponse)
async def cancel_booking(
    http_request: Request,
    booking_id: str,
    session: AsyncSession = Depends(get_db_session),
    identity: AdminIdentity = Depends(require_dispatch),
):
    booking = await session.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

    before_state = booking_schemas.BookingResponse(
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
        risk_score=booking.risk_score,
        risk_band=booking.risk_band,
        risk_reasons=booking.risk_reasons,
        cancellation_exception=booking.cancellation_exception,
        cancellation_exception_note=booking.cancellation_exception_note,
    ).model_dump(mode="json")

    try:
        booking_service.assert_valid_booking_transition(booking.status, "CANCELLED")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    booking.status = "CANCELLED"
    response_body = booking_schemas.BookingResponse(
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
        risk_score=booking.risk_score,
        risk_band=booking.risk_band,
        risk_reasons=booking.risk_reasons,
        cancellation_exception=booking.cancellation_exception,
        cancellation_exception_note=booking.cancellation_exception_note,
    )
    http_request.state.explicit_admin_audit = True
    await audit_service.record_action(
        session,
        identity=identity,
        action="booking_cancel",
        resource_type="booking",
        resource_id=booking.booking_id,
        before=before_state,
        after=response_body.model_dump(mode="json"),
    )
    await session.commit()
    return response_body


@router.post("/v1/admin/bookings/{booking_id}/reschedule", response_model=booking_schemas.BookingResponse)
async def reschedule_booking(
    http_request: Request,
    booking_id: str,
    payload: booking_schemas.BookingRescheduleRequest,
    session: AsyncSession = Depends(get_db_session),
    identity: AdminIdentity = Depends(require_dispatch),
):
    booking = await session.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
    if booking.status in {"DONE", "CANCELLED"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Booking is no longer active")

    before_state = booking_schemas.BookingResponse(
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
        risk_score=booking.risk_score,
        risk_band=booking.risk_band,
        risk_reasons=booking.risk_reasons,
        cancellation_exception=booking.cancellation_exception,
        cancellation_exception_note=booking.cancellation_exception_note,
    ).model_dump(mode="json")

    try:
        booking = await booking_service.reschedule_booking(
            session,
            booking,
            payload.starts_at,
            payload.duration_minutes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    response_body = booking_schemas.BookingResponse(
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
        risk_score=booking.risk_score,
        risk_band=booking.risk_band,
        risk_reasons=booking.risk_reasons,
        cancellation_exception=booking.cancellation_exception,
        cancellation_exception_note=booking.cancellation_exception_note,
    )
    http_request.state.explicit_admin_audit = True
    await audit_service.record_action(
        session,
        identity=identity,
        action="booking_reschedule",
        resource_type="booking",
        resource_id=booking.booking_id,
        before=before_state,
        after=response_body.model_dump(mode="json"),
    )
    await session.commit()
    return response_body


@router.post(
    "/v1/admin/bookings/{booking_id}/complete",
    response_model=booking_schemas.BookingResponse,
)
async def complete_booking(
    http_request: Request,
    booking_id: str,
    payload: booking_schemas.BookingCompletionRequest,
    session: AsyncSession = Depends(get_db_session),
    identity: AdminIdentity = Depends(require_dispatch),
):
    existing = await session.get(Booking, booking_id)
    before_state = None
    if existing:
        before_state = booking_schemas.BookingResponse(
            booking_id=existing.booking_id,
            status=existing.status,
            starts_at=existing.starts_at,
            duration_minutes=existing.duration_minutes,
            actual_duration_minutes=existing.actual_duration_minutes,
            deposit_required=existing.deposit_required,
            deposit_cents=existing.deposit_cents,
            deposit_policy=existing.deposit_policy,
            deposit_status=existing.deposit_status,
            checkout_url=None,
            risk_score=existing.risk_score,
            risk_band=existing.risk_band,
            risk_reasons=existing.risk_reasons,
            cancellation_exception=existing.cancellation_exception,
            cancellation_exception_note=existing.cancellation_exception_note,
        ).model_dump(mode="json")
    try:
        booking = await booking_service.mark_booking_completed(
            session, booking_id, payload.actual_duration_minutes
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if booking is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

    try:
        lead = await session.get(Lead, booking.lead_id) if booking.lead_id else None
        adapter = getattr(http_request.app.state, "email_adapter", None) if http_request else None
        if lead and lead.email:
            token = nps_service.issue_nps_token(
                booking.booking_id,
                client_id=booking.client_id,
                email=lead.email,
                secret=settings.client_portal_secret,
            )
            base_url = settings.public_base_url.rstrip("/") if settings.public_base_url else str(http_request.base_url).rstrip("/")
            survey_link = f"{base_url}/nps/{booking.booking_id}?token={token}"
            await email_service.send_nps_survey_email(
                session=session,
                adapter=adapter,
                booking=booking,
                lead=lead,
                survey_link=survey_link,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "nps_email_failed",
            extra={"extra": {"order_id": booking.booking_id, "reason": type(exc).__name__}},
        )

    response_body = booking_schemas.BookingResponse(
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
        risk_score=booking.risk_score,
        risk_band=booking.risk_band,
        risk_reasons=booking.risk_reasons,
        cancellation_exception=booking.cancellation_exception,
        cancellation_exception_note=booking.cancellation_exception_note,
    )
    http_request.state.explicit_admin_audit = True
    await audit_service.record_action(
        session,
        identity=identity,
        action="booking_complete",
        resource_type="booking",
        resource_id=booking.booking_id,
        before=before_state,
        after=response_body.model_dump(mode="json"),
    )
    await session.commit()
    return response_body


def _invoice_response(invoice: Invoice) -> invoice_schemas.InvoiceResponse:
    data = invoice_service.build_invoice_response(invoice)
    return invoice_schemas.InvoiceResponse(**data)


def _ticket_response(ticket: SupportTicket) -> nps_schemas.TicketResponse:
    return nps_schemas.TicketResponse(
        id=ticket.id,
        order_id=ticket.order_id,
        client_id=ticket.client_id,
        status=ticket.status,
        priority=ticket.priority,
        subject=ticket.subject,
        body=ticket.body,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
    )


def _invoice_list_item(invoice: Invoice) -> invoice_schemas.InvoiceListItem:
    data = invoice_service.build_invoice_list_item(invoice)
    return invoice_schemas.InvoiceListItem(**data)


async def _query_invoice_list(
    *,
    session: AsyncSession,
    status_filter: str | None,
    customer_id: str | None,
    order_id: str | None,
    q: str | None,
    page: int,
    page_size: int = 50,
) -> invoice_schemas.InvoiceListResponse:
    filters = []
    if status_filter:
        try:
            normalized_status = invoice_statuses.normalize_status(status_filter)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        filters.append(Invoice.status == normalized_status)
    if customer_id:
        filters.append(Invoice.customer_id == customer_id)
    if order_id:
        filters.append(Invoice.order_id == order_id)
    if q:
        filters.append(func.lower(Invoice.invoice_number).like(f"%{q.lower()}%"))

    base_query = select(Invoice).where(*filters)
    count_stmt = select(func.count()).select_from(base_query.subquery())
    total = int((await session.scalar(count_stmt)) or 0)

    stmt = (
        base_query.options(selectinload(Invoice.payments))
        .order_by(Invoice.created_at.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    result = await session.execute(stmt)
    invoices = result.scalars().all()
    return invoice_schemas.InvoiceListResponse(
        invoices=[_invoice_list_item(inv) for inv in invoices],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/v1/admin/invoices", response_model=invoice_schemas.InvoiceListResponse)
async def list_invoices(
    status_filter: str | None = Query(default=None, alias="status"),
    customer_id: str | None = None,
    order_id: str | None = None,
    q: str | None = None,
    page: int = Query(default=1, ge=1),
    session: AsyncSession = Depends(get_db_session),
    _admin: AdminIdentity = Depends(require_finance),
) -> invoice_schemas.InvoiceListResponse:
    return await _query_invoice_list(
        session=session,
        status_filter=status_filter,
        customer_id=customer_id,
        order_id=order_id,
        q=q,
        page=page,
    )


@router.get("/v1/admin/ui/invoices", response_class=HTMLResponse)
async def admin_invoice_list_ui(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    customer_id: str | None = Query(default=None),
    order_id: str | None = Query(default=None),
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    session: AsyncSession = Depends(get_db_session),
    _admin: AdminIdentity = Depends(require_finance),
) -> HTMLResponse:
    invoice_list = await _query_invoice_list(
        session=session,
        status_filter=status_filter,
        customer_id=customer_id,
        order_id=order_id,
        q=q,
        page=page,
    )

    def _cell(label: str, value: str) -> str:
        return f"<div class=\"muted small\">{html.escape(label)}: {html.escape(value)}</div>"

    rows: list[str] = []
    today = date.today()
    for invoice in invoice_list.invoices:
        overdue = invoice.status == invoice_statuses.INVOICE_STATUS_OVERDUE or (
            invoice.due_date and invoice.balance_due_cents > 0 and invoice.due_date < today
        )
        row_class = " class=\"row-highlight\"" if overdue else ""
        balance_class = "danger" if invoice.balance_due_cents > 0 else "success"
        rows.append(
            """
            <tr{row_class}>
              <td>
                <div class="title"><a href="/v1/admin/ui/invoices/{invoice_id}">{invoice_number}</a></div>
                {_id}
              </td>
              <td>{status}</td>
              <td>{issue}{due}</td>
              <td class="align-right">{total}<div class="muted small">Paid: {paid}</div></td>
              <td class="align-right {balance_class}">{balance}</td>
              <td>{order}{customer}</td>
              <td class="muted small">{created}</td>
            </tr>
            """.format(
                row_class=row_class,
                invoice_id=html.escape(invoice.invoice_id),
                invoice_number=html.escape(invoice.invoice_number),
                status=_status_badge(invoice.status),
                issue=_cell("Issue", _format_date(invoice.issue_date)),
                due=_cell("Due", _format_date(invoice.due_date)),
                total=_format_money(invoice.total_cents, invoice.currency),
                paid=_format_money(invoice.paid_cents, invoice.currency),
                balance=_format_money(invoice.balance_due_cents, invoice.currency),
                balance_class=balance_class,
                order=_cell("Order", invoice.order_id or "-"),
                customer=_cell("Customer", invoice.customer_id or "-"),
                created=_format_dt(invoice.created_at),
                _id=_cell("ID", invoice.invoice_id),
            )
        )

    table_body = "".join(rows) if rows else f"<tr><td colspan=7>{_render_empty('No invoices match these filters.')}</td></tr>"

    total_pages = max(math.ceil(invoice_list.total / invoice_list.page_size), 1)
    prev_page = invoice_list.page - 1 if invoice_list.page > 1 else None
    next_page = invoice_list.page + 1 if invoice_list.page < total_pages else None
    status_ui = status_filter.upper() if status_filter else None
    base_params = {
        "status": status_filter,
        "customer_id": customer_id,
        "order_id": order_id,
        "q": q,
    }

    pagination_parts = [
        "<div class=\"card-row\">",
        f"<div class=\"muted\">Page {invoice_list.page} of {total_pages} · {invoice_list.total} total</div>",
        "<div class=\"actions\">",
    ]
    if prev_page:
        prev_query = _build_query({**base_params, "page": prev_page})
        pagination_parts.append(f"<a class=\"btn secondary\" href=\"?{prev_query}\">Previous</a>")
    if next_page:
        next_query = _build_query({**base_params, "page": next_page})
        pagination_parts.append(f"<a class=\"btn secondary\" href=\"?{next_query}\">Next</a>")
    pagination_parts.append("</div></div>")
    pagination = "".join(pagination_parts)

    status_options = "".join(
        f'<option value="{html.escape(status)}" {"selected" if status_ui == status else ""}>{html.escape(status.title())}</option>'
        for status in sorted(invoice_statuses.INVOICE_STATUSES)
    )

    filters_html = f"""
        <form class=\"filters\" method=\"get\">
          <div class=\"form-group\">
            <label>Status</label>
            <select class=\"input\" name=\"status\">
              <option value=\"\">Any</option>
              {status_options}
            </select>
          </div>
          <div class=\"form-group\">
            <label>Customer ID</label>
            <input class=\"input\" type=\"text\" name=\"customer_id\" value=\"{html.escape(customer_id or '')}\" placeholder=\"lead id\" />
          </div>
          <div class=\"form-group\">
            <label>Order ID</label>
            <input class=\"input\" type=\"text\" name=\"order_id\" value=\"{html.escape(order_id or '')}\" placeholder=\"booking id\" />
          </div>
          <div class=\"form-group\">
            <label>Invoice #</label>
            <input class=\"input\" type=\"text\" name=\"q\" value=\"{html.escape(q or '')}\" placeholder=\"INV-2024-000001\" />
          </div>
          <div class=\"form-group\">
            <label>&nbsp;</label>
            <div class=\"actions\">
              <button class=\"btn\" type=\"submit\">Apply</button>
              <a class=\"btn secondary\" href=\"/v1/admin/ui/invoices\">Reset</a>
            </div>
          </div>
        </form>
    """

    content = "".join(
        [
            "<div class=\"card\">",
            f"<div class=\"card-row\"><div><div class=\"title with-icon\">{_icon('receipt')}<span>Invoices</span></div><div class=\"muted\">Search, filter and drill into invoices</div></div>",
            f"<div class=\"chip\">Total: {invoice_list.total}</div></div>",
            "<div class=\"muted small\">Invoices use English labels (Invoice, Subtotal, Tax, Total) regardless of your language preference.</div>",
            filters_html,
            "<table class=\"table\">",
            "<thead><tr><th>Invoice</th><th>Status</th><th>Dates</th><th>Total</th><th>Balance</th><th>Order/Customer</th><th>Created</th></tr></thead>",
            f"<tbody>{table_body}</tbody>",
            "</table>",
            pagination,
            "</div>",
        ]
    )
    return HTMLResponse(
        _wrap_page(
            request,
            content,
            title="Admin — Invoices",
            active="invoices",
            page_lang="en",
        )
    )


@router.get("/v1/admin/invoices/{invoice_id}", response_model=invoice_schemas.InvoiceResponse)
async def get_invoice(
    invoice_id: str,
    session: AsyncSession = Depends(get_db_session),
    _admin: AdminIdentity = Depends(require_finance),
) -> invoice_schemas.InvoiceResponse:
    invoice = await session.get(
        Invoice,
        invoice_id,
        options=(selectinload(Invoice.items), selectinload(Invoice.payments)),
    )
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    return _invoice_response(invoice)


@router.get("/v1/admin/ui/invoices/{invoice_id}", response_class=HTMLResponse)
async def admin_invoice_detail_ui(
    invoice_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    _admin: AdminIdentity = Depends(require_finance),
) -> HTMLResponse:
    invoice_model = await session.get(
        Invoice,
        invoice_id,
        options=(selectinload(Invoice.items), selectinload(Invoice.payments)),
    )
    if invoice_model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    lead = await invoice_service.fetch_customer(session, invoice_model)
    invoice = _invoice_response(invoice_model)
    csrf_token = get_csrf_token(request)

    customer_bits: list[str] = []
    if lead:
        contact_parts = [part for part in [lead.email, lead.phone] if part]
        contact = " · ".join(contact_parts) if contact_parts else "-"
        customer_bits.append(f"<div class=\"title\">{html.escape(lead.name)}</div>")
        customer_bits.append(f"<div class=\"muted\">{html.escape(contact)}</div>")
        if lead.address:
            customer_bits.append(f"<div class=\"muted small\">{html.escape(lead.address)}</div>")
    else:
        customer_bits.append(f"<div class=\"title\">Customer</div>")
        customer_bits.append(f"<div class=\"muted\">ID: {html.escape(invoice.customer_id or '-')}</div>")
    customer_section = "".join(
        [
            "<div class=\"card\">",
            "<div class=\"card-row\"><div class=\"title\">Customer</div>",
            f"<div class=\"muted small\">Invoice ID: {html.escape(invoice.invoice_id)} {_copy_button('Copy ID', invoice.invoice_id)}</div></div>",
            "<div class=\"stack\">",
            *customer_bits,
            f"<div class=\"muted small\">Order: {html.escape(invoice.order_id or '-')}</div>",
            "</div></div>",
        ]
    )

    items_rows = "".join(
        """
        <tr>
          <td>{desc}</td>
          <td class="align-right">{qty}</td>
          <td class="align-right">{unit}</td>
          <td class="align-right">{line}</td>
        </tr>
        """.format(
            desc=html.escape(item.description),
            qty=item.qty,
            unit=_format_money(item.unit_price_cents, invoice.currency),
            line=_format_money(item.line_total_cents, invoice.currency),
        )
        for item in invoice.items
    )
    if not items_rows:
        items_rows = f"<tr><td colspan=4>{_render_empty('No items recorded')}</td></tr>"

    payment_rows = "".join(
        """
        <tr>
          <td>{created}</td>
          <td>{provider}</td>
          <td>{method}</td>
          <td class="align-right">{amount}</td>
          <td>{status}</td>
          <td>{reference}</td>
        </tr>
        """.format(
            created=_format_dt(payment.created_at),
            provider=html.escape(payment.provider_ref or payment.provider or "-"),
            method=html.escape(payment.method),
            amount=_format_money(payment.amount_cents, payment.currency),
            status=html.escape(payment.status),
            reference=html.escape(payment.reference or "-"),
        )
        for payment in invoice.payments
    )
    if not payment_rows:
        payment_rows = f"<tr id=\"payments-empty\"><td colspan=6>{_render_empty('No payments yet')}</td></tr>"

    copy_number_btn = _copy_button("Copy number", invoice.invoice_number)
    status_badge = _status_badge(invoice.status).replace("<span", "<span id=\"status-badge\"", 1)
    overdue = invoice.status == invoice_statuses.INVOICE_STATUS_OVERDUE or (
        invoice.due_date and invoice.balance_due_cents > 0 and invoice.due_date < date.today()
    )
    balance_class = " danger" if invoice.balance_due_cents else ""
    due_class = " danger" if overdue else ""
    header = "".join(
        [
            "<div class=\"card\">",
            "<div class=\"card-row\">",
            "<div>",
            f"<div class=\"title\">Invoice {html.escape(invoice.invoice_number)}</div>",
            f"<div class=\"muted small\">{copy_number_btn} {_copy_button('Copy invoice ID', invoice.invoice_id)}</div>",
            "</div>",
            f"<div class=\"actions\">{status_badge}</div>",
            "</div>",
            "<div class=\"metric-grid\">",
            f"<div class=\"metric\"><div class=\"label\">Total</div><div id=\"total-amount\" class=\"value\">{_format_money(invoice.total_cents, invoice.currency)}</div></div>",
            f"<div class=\"metric\"><div class=\"label\">Paid</div><div id=\"paid-amount\" class=\"value\">{_format_money(invoice.paid_cents, invoice.currency)}</div></div>",
            f"<div class=\"metric\"><div class=\"label\">Balance due</div><div id=\"balance-due\" class=\"value{balance_class}\">{_format_money(invoice.balance_due_cents, invoice.currency)}</div></div>",
            f"<div class=\"metric\"><div class=\"label\">Due date</div><div id=\"due-date\" class=\"value{due_class}\">{_format_date(invoice.due_date)}</div></div>",
            "</div>",
            "<div class=\"card-row\">",
            "<div class=\"actions\">",
            "<button id=\"send-invoice-btn\" class=\"btn\" type=\"button\" onclick=\"sendInvoice()\">Send invoice</button>",
            "<span id=\"public-link-slot\"></span>",
            "</div>",
            "<div id=\"action-message\" class=\"muted small\"></div>",
            "</div>",
            "</div>",
        ]
    )

    csrf_input = render_csrf_input(csrf_token)

    payment_form = f"""
        <form id="payment-form" class="stack" onsubmit="recordPayment(event)">
          <div class="form-group">
            <label>Amount ({html.escape(invoice.currency)})</label>
            <input class="input" type="number" name="amount" step="0.01" min="0.01" placeholder="100.00" required />
          </div>
          <div class="form-group">
            <label>Method</label>
            <select class="input" name="method">
              <option value="cash">Cash</option>
              <option value="etransfer">E-transfer</option>
              <option value="card">Card</option>
              <option value="other">Other</option>
            </select>
          </div>
          <div class="form-group">
            <label>Reference</label>
            <input class="input" type="text" name="reference" placeholder="Receipt or note" />
          </div>
          {csrf_input}
          <button class="btn" type="submit">Record payment</button>
        </form>
    """

    items_table = "".join(
        [
            "<div class=\"card section\">",
            "<div class=\"card-row\"><div class=\"title\">Line items</div>",
            f"<div class=\"muted small\">{len(invoice.items)} item(s)</div></div>",
            "<table class=\"table\"><thead><tr><th>Description</th><th class=\"align-right\">Qty</th><th class=\"align-right\">Unit</th><th class=\"align-right\">Line total</th></tr></thead>",
            f"<tbody>{items_rows}</tbody>",
            "</table>",
            "</div>",
        ]
    )

    payments_table = "".join(
        [
            "<div class=\"card section\">",
            "<div class=\"card-row\"><div class=\"title\">Payments</div><div class=\"muted small\">Including manual entries</div></div>",
            "<table class=\"table\"><thead><tr><th>Created</th><th>Provider</th><th>Method</th><th class=\"align-right\">Amount</th><th>Status</th><th>Reference</th></tr></thead>",
            f"<tbody id=\"payments-table-body\">{payment_rows}</tbody>",
            "</table>",
            "</div>",
        ]
    )

    totals_block = "".join(
        [
            "<div class=\"card section\">",
            "<div class=\"title\">Totals</div>",
            "<div class=\"stack\">",
            f"<div><strong>Subtotal:</strong> {_format_money(invoice.subtotal_cents, invoice.currency)}</div>",
            f"<div><strong>Tax:</strong> {_format_money(invoice.tax_cents, invoice.currency)}</div>",
            f"<div><strong>Total:</strong> {_format_money(invoice.total_cents, invoice.currency)}</div>",
            "</div>",
            "</div>",
        ]
    )

    notes_block = ""
    if invoice.notes:
        notes_block = "".join(
            [
                "<div class=\"card section\">",
                "<div class=\"title\">Notes</div>",
                f"<div class=\"note\">{html.escape(invoice.notes)}</div>",
                "</div>",
            ]
        )

    invoice_id_json = json.dumps(invoice.invoice_id)
    currency_json = json.dumps(invoice.currency)

    script = f"""
      <script>
        const invoiceId = {invoice_id_json};
        const currency = {currency_json};

        function formatMoney(cents) {{
          return `${{currency}} ${{(cents / 100).toFixed(2)}}`;
        }}

        function getCsrfToken() {{
          const tokenInput = document.querySelector('input[name="csrf_token"]');
          return tokenInput ? tokenInput.value : '';
        }}

        function isOverdue(invoice) {{
          if (!invoice.due_date) return false;
          const today = new Date().toISOString().slice(0, 10);
          return invoice.status === "OVERDUE" || (invoice.balance_due_cents > 0 && invoice.due_date < today);
        }}

        function applyInvoiceUpdate(invoice) {{
          const statusBadge = document.getElementById('status-badge');
          if (statusBadge) {{
            statusBadge.textContent = invoice.status;
            statusBadge.className = `badge badge-status status-${{invoice.status.toLowerCase()}}`;
          }}
          const paid = document.getElementById('paid-amount');
          const balance = document.getElementById('balance-due');
          if (paid) paid.textContent = formatMoney(invoice.paid_cents);
          if (balance) {{
            balance.textContent = formatMoney(invoice.balance_due_cents);
            balance.classList.toggle('danger', invoice.balance_due_cents > 0);
          }}
          const due = document.getElementById('due-date');
          if (due) {{
            if (invoice.due_date) {{
              due.textContent = invoice.due_date;
              due.classList.toggle('danger', isOverdue(invoice));
            }} else {{
              due.textContent = '-';
              due.classList.remove('danger');
            }}
          }}
        }}

        function showPublicLink(link) {{
          const slot = document.getElementById('public-link-slot');
          if (!slot || !link) return;
          slot.innerHTML = '';
          const anchor = document.createElement('a');
          anchor.href = link;
          anchor.target = '_blank';
          anchor.className = 'btn secondary small';
          anchor.textContent = 'Public link';
          slot.appendChild(anchor);
          const copy = document.createElement('button');
          copy.type = 'button';
          copy.className = 'btn secondary small';
          copy.textContent = 'Copy link';
          copy.onclick = () => navigator.clipboard.writeText(link);
          slot.appendChild(copy);
        }}


        async function sendInvoice() {{
          const button = document.getElementById('send-invoice-btn');
          const message = document.getElementById('action-message');
          button.disabled = true;
          message.textContent = 'Sending…';
          try {{
            const response = await fetch(`/v1/admin/invoices/${{invoiceId}}/send`, {{
              method: 'POST',
              credentials: 'same-origin',
              headers: {{ 'X-CSRF-Token': getCsrfToken() }},
            }});
            let data;
            let errorDetail;
            try {{
              data = await response.json();
            }} catch (_) {{
              errorDetail = await response.text();
            }}
            if (!response.ok) {{
              throw new Error((data && data.detail) || errorDetail || response.statusText || 'Send failed');
            }}
            if (!data) {{
              throw new Error(errorDetail || 'Send failed');
            }}
            applyInvoiceUpdate(data.invoice);
            showPublicLink(data.public_link);
            message.textContent = data.email_sent ? 'Invoice emailed' : 'Public link generated';
          }} catch (err) {{
            message.textContent = `Send failed: ${{err.message}}`;
          }} finally {{
            button.disabled = false;
          }}
        }}

        function appendPaymentRow(payment) {{
          const tbody = document.getElementById('payments-table-body');
          const empty = document.getElementById('payments-empty');
          if (empty) empty.remove();
          const row = document.createElement('tr');
          const cells = [
            {{ value: payment.created_at ? new Date(payment.created_at).toLocaleString() : '-' }},
            {{ value: payment.provider_ref || payment.provider || '-' }},
            {{ value: payment.method }},
            {{ value: formatMoney(payment.amount_cents), className: 'align-right' }},
            {{ value: payment.status }},
            {{ value: payment.reference || '-' }},
          ];
          cells.forEach(({{ value, className }}) => {{
            const td = document.createElement('td');
            if (className) td.className = className;
            td.textContent = value ?? '-';
            row.appendChild(td);
          }});
          tbody.appendChild(row);
        }}


        async function recordPayment(event) {{
          event.preventDefault();
          const form = event.target;
          const message = document.getElementById('action-message');
          const amount = parseFloat(form.amount.value);
          if (Number.isNaN(amount) || amount <= 0) {{
            message.textContent = 'Amount must be greater than zero';
            return;
          }}
          const payload = {{
            amount_cents: Math.round(amount * 100),
            method: form.method.value,
            reference: form.reference.value || null,
          }};
          message.textContent = 'Recording payment…';
          try {{
            const response = await fetch(`/v1/admin/invoices/${{invoiceId}}/record-payment`, {{
              method: 'POST',
              credentials: 'same-origin',
              headers: {{ 'Content-Type': 'application/json', 'X-CSRF-Token': form.csrf_token.value }},
              body: JSON.stringify(payload),
            }});
            let data;
            let errorDetail;
            try {{
              data = await response.json();
            }} catch (_) {{
              errorDetail = await response.text();
            }}
            if (!response.ok) {{
              throw new Error((data && data.detail) || errorDetail || response.statusText || 'Payment failed');
            }}
            if (!data) {{
              throw new Error(errorDetail || 'Payment failed');
            }}
            applyInvoiceUpdate(data.invoice);
            appendPaymentRow(data.payment);
            form.reset();
            message.textContent = 'Payment recorded';
          }} catch (err) {{
            message.textContent = `Payment failed: ${{err.message}}`;
          }}
        }}

      </script>
    """

    detail_layout = "".join(
        [
            header,
            customer_section,
            items_table,
            totals_block,
            payments_table,
            "<div class=\"card section\"><div class=\"title\">Record manual payment</div>",
            payment_form,
            "</div>",
            notes_block,
            script,
        ]
    )
    response = HTMLResponse(
        _wrap_page(
            request,
            detail_layout,
            title=f"Invoice {invoice.invoice_number}",
            active="invoices",
            page_lang="en",
        )
    )
    issue_csrf_token(request, response, csrf_token)
    return response


def _format_money(cents: int, currency: str) -> str:
    return f"{currency} {cents / 100:,.2f}"


def _format_date(value: date | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d")


def _status_badge(value: str) -> str:
    normalized = value.lower()
    warning = _icon("warning") if normalized == invoice_statuses.INVOICE_STATUS_OVERDUE.lower() else ""
    return f'<span class="badge badge-status status-{normalized}"><span class="with-icon">{warning}{html.escape(value)}</span></span>'


def _addon_response(model: addon_schemas.AddonDefinitionResponse | AddonDefinition) -> addon_schemas.AddonDefinitionResponse:
    if isinstance(model, addon_schemas.AddonDefinitionResponse):
        return model
    return addon_schemas.AddonDefinitionResponse(
        addon_id=model.addon_id,
        code=model.code,
        name=model.name,
        price_cents=model.price_cents,
        default_minutes=model.default_minutes,
        is_active=model.is_active,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


@router.get(
    "/v1/admin/addons",
    response_model=list[addon_schemas.AddonDefinitionResponse],
)
async def list_addons(
    include_inactive: bool = True,
    session: AsyncSession = Depends(get_db_session),
    _admin: AdminIdentity = Depends(require_admin),
) -> list[addon_schemas.AddonDefinitionResponse]:
    addons = await addon_service.list_definitions(session, include_inactive=include_inactive)
    return [_addon_response(addon) for addon in addons]


@router.post(
    "/v1/admin/addons",
    response_model=addon_schemas.AddonDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_addon(
    payload: addon_schemas.AddonDefinitionCreate,
    session: AsyncSession = Depends(get_db_session),
    _admin: AdminIdentity = Depends(require_admin),
) -> addon_schemas.AddonDefinitionResponse:
    addon = await addon_service.create_definition(session, payload)
    await session.commit()
    await session.refresh(addon)
    return _addon_response(addon)


@router.patch(
    "/v1/admin/addons/{addon_id}",
    response_model=addon_schemas.AddonDefinitionResponse,
)
async def update_addon(
    addon_id: int,
    payload: addon_schemas.AddonDefinitionUpdate,
    session: AsyncSession = Depends(get_db_session),
    _admin: AdminIdentity = Depends(require_admin),
) -> addon_schemas.AddonDefinitionResponse:
    try:
        addon = await addon_service.update_definition(session, addon_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await session.commit()
    await session.refresh(addon)
    return _addon_response(addon)


@router.get(
    "/v1/admin/reasons",
    response_model=reason_schemas.ReasonListResponse,
)
async def admin_reason_report(
    start: datetime | None = Query(None, alias="from"),
    end: datetime | None = Query(None, alias="to"),
    kind: reason_schemas.ReasonKind | None = Query(None),
    format: str = Query("json"),
    session: AsyncSession = Depends(get_db_session),
    _admin: AdminIdentity = Depends(require_admin),
) -> Response | reason_schemas.ReasonListResponse:
    reasons = await reason_service.fetch_reasons(
        session, start=start, end=end, kind=kind
    )
    if format.lower() == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "reason_id",
                "order_id",
                "kind",
                "code",
                "note",
                "created_at",
                "created_by",
                "time_entry_id",
                "invoice_item_id",
            ]
        )
        for reason in reasons:
            writer.writerow(
                [
                    reason.reason_id,
                    reason.order_id,
                    reason.kind,
                    reason.code,
                    reason.note or "",
                    reason.created_at.isoformat(),
                    reason.created_by or "",
                    reason.time_entry_id or "",
                    reason.invoice_item_id or "",
                ]
            )
        return Response(
            content=buffer.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=reasons.csv"},
        )

    return reason_schemas.ReasonListResponse(
        reasons=[reason_schemas.ReasonResponse.from_model(reason) for reason in reasons]
    )


@router.get(
    "/v1/admin/reports/addons",
    response_model=addon_schemas.AddonReportResponse,
)
async def admin_addon_report(
    start: datetime | None = Query(None, alias="from"),
    end: datetime | None = Query(None, alias="to"),
    session: AsyncSession = Depends(get_db_session),
    _admin: AdminIdentity = Depends(require_admin),
) -> addon_schemas.AddonReportResponse:
    report = await addon_service.addon_report(session, start=start, end=end)
    return addon_schemas.AddonReportResponse(addons=report)


def _copy_button(label: str, value: str, *, small: bool = True) -> str:
    size_class = " small" if small else ""
    return (
        """
        <button type="button" class="btn secondary{size}" data-copy="{value}" onclick="navigator.clipboard.writeText(this.dataset.copy)">{icon}<span>{label}</span></button>
        """
        .replace("{size}", size_class)
        .format(
            label=html.escape(label),
            value=html.escape(value, quote=True),
            icon=_icon("copy"),
        )
    )


def _build_query(params: dict[str, str | int | None]) -> str:
    filtered = {k: v for k, v in params.items() if v not in {None, ""}}
    return urlencode(filtered, doseq=True)


@router.post(
    "/v1/admin/invoices/{invoice_id}/send",
    response_model=invoice_schemas.InvoiceSendResponse,
)
async def send_invoice(
    invoice_id: str,
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
    _csrf: None = Depends(require_csrf),
    _admin: AdminIdentity = Depends(require_finance),
) -> invoice_schemas.InvoiceSendResponse:
    invoice = await session.get(
        Invoice,
        invoice_id,
        options=(selectinload(Invoice.items), selectinload(Invoice.payments)),
    )
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    lead = await invoice_service.fetch_customer(session, invoice)
    if lead is None or not lead.email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invoice missing customer email")

    token = await invoice_service.upsert_public_token(session, invoice, mark_sent=True)
    base_url = settings.public_base_url.rstrip("/") if settings.public_base_url else None
    if base_url:
        public_link = f"{base_url}/i/{token}"
        public_link_pdf = f"{base_url}/i/{token}.pdf"
    else:
        public_link = str(http_request.url_for("public_invoice_view", token=token))
        public_link_pdf = str(http_request.url_for("public_invoice_pdf", token=token))

    adapter = getattr(http_request.app.state, "email_adapter", None)
    if adapter is None:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Email adapter unavailable")

    subject = f"Invoice {invoice.invoice_number}"
    body = (
        f"Hi {lead.name},\n\n"
        f"Here's your invoice ({invoice.invoice_number}).\n"
        f"View online: {public_link}\n"
        f"Download PDF: {public_link_pdf}\n"
        f"Total due: {_format_money(invoice.total_cents, invoice.currency)}\n\n"
        "If you have questions, reply to this email."
    )
    try:
        delivered = await adapter.send_email(recipient=lead.email, subject=subject, body=body)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "invoice_email_failed",
            extra={"extra": {"invoice_id": invoice.invoice_id, "reason": type(exc).__name__}},
        )
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Email send failed") from exc

    if not delivered:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Email send failed")

    if invoice.status == invoice_statuses.INVOICE_STATUS_DRAFT:
        invoice.status = invoice_statuses.INVOICE_STATUS_SENT

    await session.commit()
    refreshed = await session.get(
        Invoice,
        invoice.invoice_id,
        options=(selectinload(Invoice.items), selectinload(Invoice.payments)),
    )
    assert refreshed is not None
    await session.refresh(refreshed)
    invoice_response = invoice_schemas.InvoiceResponse(
        **invoice_service.build_invoice_response(refreshed)
    )
    return invoice_schemas.InvoiceSendResponse(
        invoice=invoice_response,
        public_link=public_link,
        email_sent=bool(delivered),
    )


@router.post(
    "/v1/admin/orders/{order_id}/invoice",
    response_model=invoice_schemas.InvoiceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_invoice_from_order(
    order_id: str,
    request: invoice_schemas.InvoiceCreateRequest,
    session: AsyncSession = Depends(get_db_session),
    _csrf: None = Depends(require_csrf),
    admin: AdminIdentity = Depends(require_finance),
) -> invoice_schemas.InvoiceResponse:
    order = await session.get(
        Booking, order_id, options=(selectinload(Booking.lead),)
    )
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    addon_items = await addon_service.addon_invoice_items_for_order(session, order_id)
    base_items = list(request.items)
    all_items = [*base_items, *addon_items]

    expected_subtotal = reason_service.estimate_subtotal_from_lead(order.lead)
    requested_subtotal = sum(item.qty * item.unit_price_cents for item in base_items)
    if (
        expected_subtotal is not None
        and requested_subtotal != expected_subtotal
        and not await reason_service.has_reason(
            session, order_id, kind=reason_schemas.ReasonKind.PRICE_ADJUST
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="PRICE_ADJUST reason required for invoice change",
        )

    try:
        invoice = await invoice_service.create_invoice_from_order(
            session=session,
            order=order,
            items=all_items,
            issue_date=request.issue_date,
            due_date=request.due_date,
            currency=request.currency,
            notes=request.notes,
            created_by=admin.username or admin.role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await session.commit()
    result = await session.execute(
        select(Invoice)
        .options(selectinload(Invoice.items), selectinload(Invoice.payments))
        .where(Invoice.invoice_id == invoice.invoice_id)
    )
    fresh_invoice = result.scalar_one()
    return _invoice_response(fresh_invoice)


@router.post(
    "/v1/admin/invoices/{invoice_id}/mark-paid",
    response_model=invoice_schemas.ManualPaymentResult,
    status_code=status.HTTP_201_CREATED,
)
async def mark_invoice_paid(
    invoice_id: str,
    request: invoice_schemas.ManualPaymentRequest,
    session: AsyncSession = Depends(get_db_session),
    _csrf: None = Depends(require_csrf),
    _admin: AdminIdentity = Depends(require_finance),
) -> invoice_schemas.ManualPaymentResult:
    return await _record_manual_invoice_payment(invoice_id, request, session)


@router.post(
    "/v1/admin/invoices/{invoice_id}/record-payment",
    response_model=invoice_schemas.ManualPaymentResult,
    status_code=status.HTTP_201_CREATED,
)
async def record_manual_invoice_payment(
    invoice_id: str,
    request: invoice_schemas.ManualPaymentRequest,
    session: AsyncSession = Depends(get_db_session),
    _csrf: None = Depends(require_csrf),
    _admin: AdminIdentity = Depends(require_finance),
) -> invoice_schemas.ManualPaymentResult:
    return await _record_manual_invoice_payment(invoice_id, request, session)


async def _record_manual_invoice_payment(
    invoice_id: str,
    request: invoice_schemas.ManualPaymentRequest,
    session: AsyncSession,
) -> invoice_schemas.ManualPaymentResult:
    invoice = await session.get(
        Invoice,
        invoice_id,
        options=(selectinload(Invoice.items), selectinload(Invoice.payments)),
    )
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    try:
        payment = await invoice_service.record_manual_payment(
            session=session,
            invoice=invoice,
            amount_cents=request.amount_cents,
            method=request.method,
            reference=request.reference,
            received_at=request.received_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await session.commit()
    await session.refresh(payment)
    refreshed_invoice = await session.get(
        Invoice,
        invoice_id,
        options=(selectinload(Invoice.items), selectinload(Invoice.payments)),
    )
    assert refreshed_invoice is not None
    await session.refresh(refreshed_invoice)
    payment_data = invoice_schemas.PaymentResponse(
        payment_id=payment.payment_id,
        provider=payment.provider,
        provider_ref=payment.provider_ref,
        method=payment.method,
        amount_cents=payment.amount_cents,
        currency=payment.currency,
        status=payment.status,
        received_at=payment.received_at,
        reference=payment.reference,
        created_at=payment.created_at,
    )
    return invoice_schemas.ManualPaymentResult(
        invoice=_invoice_response(refreshed_invoice),
        payment=payment_data,
    )


@router.get("/api/admin/tickets", response_model=nps_schemas.TicketListResponse)
async def list_support_tickets(
    session: AsyncSession = Depends(get_db_session),
    _identity: AdminIdentity = Depends(require_admin),
) -> nps_schemas.TicketListResponse:
    tickets = await nps_service.list_tickets(session)
    return nps_schemas.TicketListResponse(tickets=[_ticket_response(ticket) for ticket in tickets])


@router.patch("/api/admin/tickets/{ticket_id}", response_model=nps_schemas.TicketResponse)
async def update_support_ticket(
    ticket_id: str,
    payload: nps_schemas.TicketUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
    _identity: AdminIdentity = Depends(require_admin),
) -> nps_schemas.TicketResponse:
    try:
        ticket = await nps_service.update_ticket_status(session, ticket_id, payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    await session.commit()
    await session.refresh(ticket)
    return _ticket_response(ticket)


def _worker_status_badge(worker: Worker, lang: str | None) -> str:
    label = tr(lang, "admin.workers.status_active") if worker.is_active else tr(lang, "admin.workers.status_inactive")
    cls = "badge" + (" badge-active" if worker.is_active else "")
    return f'<span class="{cls}">{html.escape(label)}</span>'


def _render_worker_form(worker: Worker | None, teams: list[Team], lang: str | None, csrf_input: str) -> str:
    team_options = "".join(
        f'<option value="{team.team_id}" {"selected" if worker and worker.team_id == team.team_id else ""}>{html.escape(team.name)}</option>'
        for team in teams
    )
    hourly_rate = getattr(worker, "hourly_rate_cents", None)
    return f"""
    <div class=\"card\">
      <div class=\"card-row\">
        <div>
          <div class=\"title with-icon\">{_icon('users')}{html.escape(tr(lang, 'admin.workers.title'))}</div>
          <div class=\"muted\">{html.escape(tr(lang, 'admin.workers.subtitle'))}</div>
        </div>
      </div>
      <form class=\"stack\" method=\"post\">
        <div class=\"form-group\">
          <label>{html.escape(tr(lang, 'admin.workers.name'))}</label>
          <input class=\"input\" type=\"text\" name=\"name\" required value=\"{html.escape(getattr(worker, 'name', '') or '')}\" />
        </div>
        <div class=\"form-group\">
          <label>{html.escape(tr(lang, 'admin.workers.phone'))}</label>
          <input class=\"input\" type=\"text\" name=\"phone\" required value=\"{html.escape(getattr(worker, 'phone', '') or '')}\" />
        </div>
        <div class=\"form-group\">
          <label>{html.escape(tr(lang, 'admin.workers.email'))}</label>
          <input class=\"input\" type=\"email\" name=\"email\" value=\"{html.escape(getattr(worker, 'email', '') or '')}\" />
        </div>
        <div class=\"form-group\">
          <label>{html.escape(tr(lang, 'admin.workers.role'))}</label>
          <input class=\"input\" type=\"text\" name=\"role\" value=\"{html.escape(getattr(worker, 'role', '') or '')}\" />
        </div>
        <div class=\"form-group\">
          <label>{html.escape(tr(lang, 'admin.workers.hourly_rate'))}</label>
          <input class=\"input\" type=\"number\" name=\"hourly_rate_cents\" min=\"0\" step=\"50\" value=\"{'' if hourly_rate is None else hourly_rate}\" />
        </div>
        <div class=\"form-group\">
          <label>{html.escape(tr(lang, 'admin.workers.team'))}</label>
          <select class=\"input\" name=\"team_id\" required>{team_options}</select>
        </div>
        <div class=\"actions\">
          <label class=\"with-icon\"><input type=\"checkbox\" name=\"is_active\" {'' if worker and not worker.is_active else 'checked'} /> {html.escape(tr(lang, 'admin.workers.is_active'))}</label>
        </div>
        {csrf_input}
        <button class=\"btn\" type=\"submit\">{html.escape(tr(lang, 'admin.workers.save'))}</button>
      </form>
    </div>
    """


async def _list_workers(
    session: AsyncSession, *, q: str | None, active_only: bool, team_id: int | None
) -> list[Worker]:
    filters = []
    if active_only:
        filters.append(Worker.is_active.is_(True))
    if team_id:
        filters.append(Worker.team_id == team_id)
    if q:
        pattern = f"%{q.lower()}%"
        filters.append(
            or_(
                func.lower(Worker.name).like(pattern),
                func.lower(Worker.phone).like(pattern),
                func.lower(Worker.email).like(pattern),
            )
        )
    stmt = select(Worker).where(*filters).options(selectinload(Worker.team)).order_by(Worker.created_at.desc())
    result = await session.execute(stmt)
    return result.scalars().all()


@router.get("/v1/admin/ui/workers", response_class=HTMLResponse)
async def admin_workers_list(
    request: Request,
    q: str | None = Query(default=None),
    active_only: bool = Query(default=False),
    team_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    _identity: AdminIdentity = Depends(require_dispatch),
) -> HTMLResponse:
    lang = resolve_lang(request)
    workers = await _list_workers(session, q=q, active_only=active_only, team_id=team_id)
    teams = (await session.execute(select(Team).order_by(Team.name))).scalars().all()
    team_filter_options = "".join(
        f'<option value="{team.team_id}" {"selected" if team_id == team.team_id else ""}>{html.escape(team.name)}</option>'
        for team in teams
    )
    cards: list[str] = []
    for worker in workers:
        contact = [worker.phone]
        if worker.email:
            contact.append(worker.email)
        cards.append(
            """
            <div class=\"card\">
              <div class=\"card-row\">
                <div>
                  <div class=\"title with-icon\">{icon}{name}</div>
                  <div class=\"muted\">{team}</div>
                  <div class=\"muted\">{contact_label}: {contact}</div>
                </div>
                <div class=\"actions\">{status}<a class=\"btn secondary small\" href=\"/v1/admin/ui/workers/{worker_id}\">{edit_icon}{edit_label}</a></div>
              </div>
              <div class=\"muted small\">{role}</div>
            </div>
            """.format(
                icon=_icon("users"),
                name=html.escape(worker.name),
                team=html.escape(getattr(worker.team, "name", tr(lang, "admin.workers.team"))),
                contact_label=html.escape(tr(lang, "admin.workers.contact")),
                contact=html.escape(" · ".join(filter(None, contact))),
                status=_worker_status_badge(worker, lang),
                worker_id=worker.worker_id,
                edit_icon=_icon("edit"),
                edit_label=html.escape(tr(lang, "admin.workers.save")),
                role=html.escape(worker.role or tr(lang, "admin.workers.role")),
            )
        )
    content = "".join(
        [
            "<div class=\"card\">",
            "<div class=\"card-row\">",
            f"<div><div class=\"title with-icon\">{_icon('users')}{html.escape(tr(lang, 'admin.workers.title'))}</div>",
            f"<div class=\"muted\">{html.escape(tr(lang, 'admin.workers.subtitle'))}</div></div>",
            "<div class=\"actions\">",
            f"<a class=\"btn\" href=\"/v1/admin/ui/workers/new\">{_icon('plus')}{html.escape(tr(lang, 'admin.workers.create'))}</a>",
            "</div></div>",
            "<form class=\"filters\" method=\"get\">",
            f"<div class=\"form-group\"><label>{html.escape(tr(lang, 'admin.workers.search'))}</label><input class=\"input\" type=\"text\" name=\"q\" value=\"{html.escape(q or '')}\" /></div>",
            f"<div class=\"form-group\"><label>{html.escape(tr(lang, 'admin.workers.team'))}</label><select class=\"input\" name=\"team_id\"><option value=\"\"></option>{team_filter_options}</select></div>",
            f"<div class=\"form-group\"><label>{html.escape(tr(lang, 'admin.workers.active_only'))}</label><input type=\"checkbox\" name=\"active_only\" value=\"1\" { 'checked' if active_only else '' } /></div>",
            "<div class=\"form-group\"><label>&nbsp;</label><div class=\"actions\"><button class=\"btn\" type=\"submit\">Apply</button><a class=\"btn secondary\" href=\"/v1/admin/ui/workers\">Reset</a></div></div>",
            "</form>",
            "</div>",
            "<div class=\"stack\">{cards}</div>".format(cards="".join(cards) or _render_empty(tr(lang, "admin.workers.none"))),
        ]
    )
    return HTMLResponse(_wrap_page(request, content, title="Admin — Workers", active="workers", page_lang=lang))


@router.get("/v1/admin/ui/workers/new", response_class=HTMLResponse)
async def admin_workers_new_form(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    _identity: AdminIdentity = Depends(require_dispatch),
) -> HTMLResponse:
    lang = resolve_lang(request)
    teams = (await session.execute(select(Team).order_by(Team.name))).scalars().all()
    csrf_token = get_csrf_token(request)
    response = HTMLResponse(
        _wrap_page(
            request,
            _render_worker_form(None, teams, lang, render_csrf_input(csrf_token)),
            title="Admin — Workers",
            active="workers",
            page_lang=lang,
        )
    )
    issue_csrf_token(request, response, csrf_token)
    return response


@router.post("/v1/admin/ui/workers/new", response_class=HTMLResponse)
async def admin_workers_create(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    identity: AdminIdentity = Depends(require_dispatch),
) -> Response:
    await require_csrf(request)
    form = await request.form()
    name = (form.get("name") or "").strip()
    phone = (form.get("phone") or "").strip()
    email = (form.get("email") or "").strip() or None
    role = (form.get("role") or "").strip() or None
    team_id_raw = form.get("team_id")
    hourly_rate_raw = form.get("hourly_rate_cents")
    is_active = (
        form.get("is_active") == "on"
        or form.get("is_active") == "1"
        or "is_active" not in form
    )

    if not name or not phone or not team_id_raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing required fields")
    team_id = int(team_id_raw)
    team = await session.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    hourly_rate_cents = int(hourly_rate_raw) if hourly_rate_raw else None
    worker = Worker(
        name=name,
        phone=phone,
        email=email,
        role=role,
        team_id=team_id,
        hourly_rate_cents=hourly_rate_cents,
        is_active=is_active,
    )
    session.add(worker)
    await audit_service.record_action(
        session,
        identity=identity,
        action="CREATE_WORKER",
        resource_type="worker",
        resource_id=None,
        before=None,
        after={
            "name": name,
            "phone": phone,
            "email": email,
            "role": role,
            "team_id": team_id,
            "hourly_rate_cents": hourly_rate_cents,
            "is_active": is_active,
        },
    )
    await session.commit()
    return RedirectResponse("/v1/admin/ui/workers", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/v1/admin/ui/workers/{worker_id}", response_class=HTMLResponse)
async def admin_workers_edit_form(
    worker_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    _identity: AdminIdentity = Depends(require_dispatch),
) -> HTMLResponse:
    lang = resolve_lang(request)
    worker = await session.get(Worker, worker_id)
    if worker is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker not found")
    teams = (await session.execute(select(Team).order_by(Team.name))).scalars().all()
    csrf_token = get_csrf_token(request)
    response = HTMLResponse(
        _wrap_page(
            request,
            _render_worker_form(worker, teams, lang, render_csrf_input(csrf_token)),
            title="Admin — Workers",
            active="workers",
            page_lang=lang,
        )
    )
    issue_csrf_token(request, response, csrf_token)
    return response


@router.post("/v1/admin/ui/workers/{worker_id}", response_class=HTMLResponse)
async def admin_workers_update(
    worker_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    identity: AdminIdentity = Depends(require_dispatch),
) -> Response:
    await require_csrf(request)
    worker = await session.get(Worker, worker_id)
    if worker is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker not found")

    before = {
        "name": worker.name,
        "phone": worker.phone,
        "email": worker.email,
        "role": worker.role,
        "team_id": worker.team_id,
        "hourly_rate_cents": worker.hourly_rate_cents,
        "is_active": worker.is_active,
    }

    form = await request.form()
    worker.name = (form.get("name") or worker.name).strip()
    worker.phone = (form.get("phone") or worker.phone).strip()
    worker.email = (form.get("email") or "").strip() or None
    worker.role = (form.get("role") or "").strip() or None
    team_id_raw = form.get("team_id")
    if team_id_raw:
        team = await session.get(Team, int(team_id_raw))
        if team is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
        worker.team_id = team.team_id
    hourly_rate_raw = form.get("hourly_rate_cents")
    worker.hourly_rate_cents = int(hourly_rate_raw) if hourly_rate_raw else None
    if "is_active" in form:
        worker.is_active = form.get("is_active") == "on" or form.get("is_active") == "1"

    await audit_service.record_action(
        session,
        identity=identity,
        action="UPDATE_WORKER",
        resource_type="worker",
        resource_id=str(worker.worker_id),
        before=before,
        after={
            "name": worker.name,
            "phone": worker.phone,
            "email": worker.email,
            "role": worker.role,
            "team_id": worker.team_id,
            "hourly_rate_cents": worker.hourly_rate_cents,
            "is_active": worker.is_active,
        },
    )
    await session.commit()
    return RedirectResponse("/v1/admin/ui/workers", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/v1/admin/ui/dispatch", response_class=HTMLResponse)
async def admin_dispatch_board(
    request: Request,
    day: str | None = Query(default=None, alias="date"),
    session: AsyncSession = Depends(get_db_session),
    _identity: AdminIdentity = Depends(require_dispatch),
) -> HTMLResponse:
    lang = resolve_lang(request)
    try:
        target_date = date.fromisoformat(day) if day else datetime.now(timezone.utc).date()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid date") from exc
    start_dt = datetime.combine(target_date, time.min).replace(tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(days=1)

    stmt = (
        select(Booking)
        .where(Booking.starts_at >= start_dt, Booking.starts_at < end_dt)
        .options(
            selectinload(Booking.lead),
            selectinload(Booking.assigned_worker),
            selectinload(Booking.team),
        )
        .order_by(Booking.starts_at)
    )
    bookings = (await session.execute(stmt)).scalars().all()
    team_ids = {booking.team_id for booking in bookings}
    workers_stmt = select(Worker).where(Worker.team_id.in_(team_ids or {0})).options(selectinload(Worker.team)).order_by(Worker.name)
    workers = (await session.execute(workers_stmt)).scalars().all()
    workers_by_team: dict[int, list[Worker]] = {}
    for worker in workers:
        workers_by_team.setdefault(worker.team_id, []).append(worker)

    csrf_token = get_csrf_token(request)
    csrf_input = render_csrf_input(csrf_token)

    cards: list[str] = []
    for booking in bookings:
        lead: Lead | None = getattr(booking, "lead", None)
        assigned = getattr(booking, "assigned_worker", None)
        worker_options = [
            f'<option value="">{html.escape(tr(lang, "admin.dispatch.unassigned"))}</option>'
        ]
        for worker in workers_by_team.get(booking.team_id, []):
            selected = "selected" if worker.worker_id == getattr(booking, "assigned_worker_id", None) else ""
            status_hint = "" if worker.is_active else " (inactive)"
            worker_options.append(
                f'<option value="{worker.worker_id}" {selected}>{html.escape(worker.name + status_hint)}</option>'
            )
        cards.append(
            """
            <div class=\"card\">
              <div class=\"card-row\">
                <div>
                  <div class=\"title\">{starts}</div>
                  <div class=\"muted\">{customer}</div>
                  <div class=\"muted\">{team_label}: {team}</div>
                </div>
                <div class=\"actions\">{status}</div>
              </div>
              <form class=\"actions\" method=\"post\" action=\"/v1/admin/ui/dispatch/assign\">
                <input type=\"hidden\" name=\"booking_id\" value=\"{booking_id}\" />
                {csrf_input}
                <label class=\"muted\">{assign_label}</label>
                <select class=\"input\" name=\"worker_id\">{options}</select>
                <button class=\"btn secondary\" type=\"submit\">{save_label}</button>
              </form>
              <div class=\"muted small\">{current}</div>
            </div>
            """.format(
                starts=html.escape(_format_dt(booking.starts_at)),
                customer=html.escape(getattr(lead, "name", tr(lang, "admin.dispatch.customer"))),
                team_label=html.escape(tr(lang, "admin.dispatch.team")),
                team=html.escape(getattr(booking.team, "name", "")),
                status=html.escape(booking.status),
                booking_id=html.escape(booking.booking_id),
                assign_label=html.escape(tr(lang, "admin.dispatch.assigned_worker")),
                options="".join(worker_options),
                save_label=html.escape(tr(lang, "admin.dispatch.save")),
                current=html.escape(
                    getattr(assigned, "name", tr(lang, "admin.dispatch.unassigned"))
                ),
                csrf_input=csrf_input,
            )
        )

    date_value = target_date.isoformat()
    content = "".join(
        [
            "<div class=\"card\">",
            "<div class=\"card-row\">",
            f"<div><div class=\"title with-icon\">{_icon('calendar')}{html.escape(tr(lang, 'admin.dispatch.title'))}</div>",
            f"<div class=\"muted\">{html.escape(tr(lang, 'admin.dispatch.subtitle'))}</div></div>",
            "<form class=\"actions\" method=\"get\">",
            f"<label class=\"muted\">{html.escape(tr(lang, 'admin.dispatch.date_label'))}</label>",
            f"<input class=\"input\" type=\"date\" name=\"date\" value=\"{date_value}\" />",
            "<button class=\"btn secondary\" type=\"submit\">Go</button>",
            "</form>",
            "</div>",
            f"<div class=\"stack\">{''.join(cards) if cards else _render_empty(tr(lang, 'admin.workers.none'))}</div>",
            "</div>",
        ]
    )
    response = HTMLResponse(_wrap_page(request, content, title="Admin — Dispatch", active="dispatch", page_lang=lang))
    issue_csrf_token(request, response, csrf_token)
    return response


@router.post("/v1/admin/ui/dispatch/assign", response_class=HTMLResponse)
async def admin_assign_worker(
    request: Request,
    booking_id: str = Form(...),
    worker_id_raw: str | None = Form(default=None, alias="worker_id"),
    session: AsyncSession = Depends(get_db_session),
    identity: AdminIdentity = Depends(require_dispatch),
) -> Response:
    await require_csrf(request)
    booking = await session.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

    previous = getattr(booking, "assigned_worker_id", None)
    new_worker_id: int | None
    if worker_id_raw is None or str(worker_id_raw).strip() == "":
        new_worker_id = None
    else:
        try:
            new_worker_id = int(worker_id_raw)
        except (TypeError, ValueError) as exc:  # noqa: BLE001
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid worker ID") from exc

    if new_worker_id is not None:
        worker = await session.get(Worker, new_worker_id)
        if worker is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker not found")
        if worker.team_id != booking.team_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Worker must be on the same team")
    booking.assigned_worker_id = new_worker_id

    await audit_service.record_action(
        session,
        identity=identity,
        action="ASSIGN_WORKER" if booking.assigned_worker_id else "UNASSIGN_WORKER",
        resource_type="booking",
        resource_id=booking.booking_id,
        before={"assigned_worker_id": previous},
        after={"assigned_worker_id": booking.assigned_worker_id},
    )
    await session.commit()
    target_date = getattr(booking.starts_at, "date", lambda: None)() if hasattr(booking.starts_at, "date") else None
    redirect_url = "/v1/admin/ui/dispatch"
    if target_date:
        redirect_url = f"{redirect_url}?date={target_date.isoformat()}"
    return RedirectResponse(redirect_url, status_code=status.HTTP_303_SEE_OTHER)
