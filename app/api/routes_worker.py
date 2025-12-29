import html
import logging
from collections import defaultdict
from datetime import datetime, timezone
from math import ceil
from typing import Iterable

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.worker_auth import (
    SESSION_COOKIE_NAME,
    WorkerIdentity,
    _session_token,
    get_worker_identity,
    require_worker,
)
from app.dependencies import get_db_session
from app.domain.addons import service as addon_service
from app.domain.analytics import service as analytics_service
from app.domain.bookings.db_models import Booking
from app.domain.addons.db_models import OrderAddon
from app.domain.admin_audit import service as audit_service
from app.domain.invoices.db_models import Invoice
from app.domain.leads.db_models import Lead
from app.domain.reason_logs import schemas as reason_schemas
from app.domain.reason_logs import service as reason_service
from app.domain.time_tracking import schemas as time_schemas
from app.domain.time_tracking import service as time_service
from app.settings import settings

router = APIRouter()
logger = logging.getLogger(__name__)


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return "-"
    localized = value
    if value.tzinfo is None:
        localized = value.replace(tzinfo=timezone.utc)
    return localized.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _status_badge(status: str) -> str:
    return f'<span class="badge badge-active">{html.escape(status)}</span>'


def _risk_badge(booking: Booking) -> str:
    if not booking.risk_band or booking.risk_band == "LOW":
        return ""
    reasons = ", ".join(booking.risk_reasons or []) or booking.risk_band
    return f'<span class="chip danger">Risk: {html.escape(booking.risk_band)} — {html.escape(reasons)}</span>'


def _deposit_badge(booking: Booking, invoice: Invoice | None) -> str:
    parts: list[str] = []
    if booking.deposit_required:
        label = "Deposit required"
        if booking.deposit_status:
            label = f"Deposit: {booking.deposit_status}"
        parts.append(label)
    if invoice:
        parts.append(f"Invoice {invoice.status.title()}")
    if not parts:
        return ""
    return "".join(f'<span class="badge">{html.escape(part)}</span>' for part in parts)


async def _audit(
    session: AsyncSession,
    identity: WorkerIdentity,
    *,
    action: str,
    resource_type: str | None,
    resource_id: str | None,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    await audit_service.record_action(
        session,
        identity=identity,  # type: ignore[arg-type]
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        before=before,
        after=after,
    )
    await session.commit()


async def _load_team_bookings(session: AsyncSession, team_id: int) -> list[Booking]:
    stmt = (
        select(Booking)
        .where(Booking.team_id == team_id)
        .options(
            selectinload(Booking.lead),
            selectinload(Booking.order_addons).selectinload(OrderAddon.definition),
        )
        .order_by(Booking.starts_at)
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def _latest_invoices(session: AsyncSession, order_ids: list[str]) -> dict[str, Invoice]:
    if not order_ids:
        return {}
    stmt = (
        select(Invoice)
        .where(Invoice.order_id.in_(order_ids))
        .order_by(Invoice.order_id, Invoice.created_at.desc())
    )
    result = await session.execute(stmt)
    invoices: dict[str, Invoice] = {}
    for invoice in result.scalars().all():
        if invoice.order_id:
            invoices.setdefault(invoice.order_id, invoice)
    return invoices


async def _load_worker_booking(
    session: AsyncSession, job_id: str, identity: WorkerIdentity
) -> Booking:
    booking = await session.get(Booking, job_id)
    if booking is None or booking.team_id != identity.team_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return booking


async def _record_job_event(
    session: AsyncSession, booking: Booking, event_type: analytics_service.EventType
) -> None:
    lead = booking.__dict__.get("lead") if hasattr(booking, "__dict__") else None
    try:
        await analytics_service.log_event(
            session,
            event_type=event_type,
            lead=lead,
            booking=booking,
            estimated_duration_minutes=booking.planned_minutes
            if booking.planned_minutes is not None
            else booking.duration_minutes,
            actual_duration_minutes=booking.actual_duration_minutes,
        )
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception(
            "worker_job_event_failed",
            extra={"extra": {"booking_id": booking.booking_id, "event_type": event_type}},
        )


def _wrap_page(body: str, *, title: str = "Worker", active: str | None = None) -> str:
    nav_links = [
        ("Dashboard", "/worker", "dashboard"),
        ("My Jobs", "/worker/jobs", "jobs"),
    ]
    nav = "".join(
        f'<a class="nav-link{" nav-link-active" if active == key else ""}" href="{href}">{html.escape(label)}</a>'
        for label, href, key in nav_links
    )
    return f"""
    <html>
      <head>
        <title>{html.escape(title)}</title>
        <style>
          :root {{ color-scheme: light; }}
          body {{ font-family: Arial, sans-serif; margin: 12px; background: #f8fafc; color: #0f172a; }}
          .page {{ max-width: 720px; margin: 0 auto; padding: 12px; }}
          .topbar {{ display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }}
          .nav {{ display: flex; gap: 8px; flex-wrap: wrap; }}
          .nav-link {{ text-decoration: none; color: #334155; padding: 6px 10px; border-radius: 999px; border: 1px solid transparent; font-size: 14px; }}
          .nav-link-active {{ background: #0f172a; color: #fff; border-color: #0f172a; }}
          h1 {{ margin: 8px 0; font-size: 22px; }}
          h2 {{ margin: 16px 0 8px; font-size: 18px; }}
          .card {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 12px; margin-bottom: 12px; box-shadow: 0 1px 2px rgba(0,0,0,0.04); }}
          .row {{ display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }}
          .muted {{ color: #64748b; font-size: 13px; }}
          .badge {{ display: inline-flex; align-items: center; padding: 4px 8px; border-radius: 999px; border: 1px solid #cbd5e1; font-size: 12px; color: #0f172a; margin-right: 4px; }}
          .badge-active {{ background: #0f172a; color: #fff; border-color: #0f172a; }}
          .chip {{ display: inline-flex; align-items: center; gap: 6px; padding: 6px 10px; border-radius: 10px; border: 1px solid #fecdd3; background: #fff1f2; color: #b91c1c; font-size: 13px; }}
          .list {{ display: flex; flex-direction: column; gap: 8px; }}
          .title {{ font-weight: 700; font-size: 16px; }}
          .pill {{ display: inline-flex; align-items: center; gap: 4px; padding: 6px 8px; border-radius: 10px; border: 1px solid #e2e8f0; background: #f8fafc; font-size: 12px; }}
          .stack {{ display: flex; flex-direction: column; gap: 6px; }}
          table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
          th, td {{ text-align: left; padding: 6px 4px; border-bottom: 1px solid #e2e8f0; }}
          .actions {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 8px; }}
          .button {{ display: inline-flex; align-items: center; justify-content: center; padding: 8px 12px; border-radius: 10px; border: 1px solid #cbd5e1; background: #fff; color: #0f172a; text-decoration: none; cursor: pointer; font-weight: 600; }}
          .button.primary {{ background: #0f172a; color: #fff; border-color: #0f172a; }}
          .button.secondary {{ background: #e2e8f0; color: #0f172a; }}
          .button.danger {{ background: #fee2e2; color: #b91c1c; border-color: #fca5a5; }}
          form.inline {{ display: inline; margin: 0; }}
          .stack-tight {{ display: flex; flex-direction: column; gap: 4px; }}
          .alert {{ border: 1px solid #cbd5e1; background: #f8fafc; padding: 10px; border-radius: 10px; margin-bottom: 12px; }}
          .alert.error {{ border-color: #fecdd3; background: #fff1f2; color: #b91c1c; }}
        </style>
      </head>
      <body>
        <div class="page">
          <div class="topbar">
            <div class="title">Worker Portal</div>
            <div class="nav">{nav}</div>
          </div>
          {body}
        </div>
      </body>
    </html>
    """


def _render_job_card(booking: Booking, invoice: Invoice | None) -> str:
    lead: Lead | None = getattr(booking, "lead", None)
    badges = " ".join(filter(None, [_status_badge(booking.status), _risk_badge(booking), _deposit_badge(booking, invoice)]))
    address = lead.address if lead else "Address on file"
    return f"""
    <div class="card">
      <div class="row">
        <div>
          <div class="title"><a href="/worker/jobs/{booking.booking_id}">{booking.booking_id}</a></div>
          <div class="muted">{html.escape(address or 'Address pending')}</div>
          <div class="muted">{_format_dt(booking.starts_at)} · {booking.duration_minutes} mins</div>
        </div>
        <div class="stack" style="align-items:flex-end;">{badges}</div>
      </div>
    </div>
    """


def _render_dashboard(next_job: Booking | None, counts: dict[str, int]) -> str:
    metrics = "".join(
        f"<div class=\"pill\"><strong>{html.escape(status.title())}</strong>: {count}</div>"
        for status, count in sorted(counts.items())
    )
    next_card = "<p class=\"muted\">No jobs assigned.</p>"
    if next_job:
        next_card = _render_job_card(next_job, None)
    return f"""
    <h1>Today</h1>
    <div class="stack">{metrics}</div>
    <h2>Next job</h2>
    {next_card}
    """


def _cancellation_summary(booking: Booking) -> str:
    policy = booking.policy_snapshot or {}
    cancellation = policy.get("cancellation", {}) if isinstance(policy, dict) else getattr(policy, "cancellation", None)
    if not cancellation:
        return "Not available"
    rules = getattr(cancellation, "rules", None) or cancellation.get("rules", [])
    windows = getattr(cancellation, "windows", None) or cancellation.get("windows", [])
    parts = []
    for window in windows:
        label = window.label if hasattr(window, "label") else window.get("label")
        start = window.start_hours_before if hasattr(window, "start_hours_before") else window.get("start_hours_before")
        end = window.end_hours_before if hasattr(window, "end_hours_before") else window.get("end_hours_before")
        refund = window.refund_percent if hasattr(window, "refund_percent") else window.get("refund_percent")
        parts.append(f"{label}: {refund}% refund ({start}h to {end}h before)")
    if rules:
        parts.extend(rules)
    return "; ".join(parts)


def _reason_options(codes: set[reason_schemas.ReasonCode]) -> str:
    options = ['<option value="">No reason</option>']
    for code in sorted(codes, key=lambda c: c.value):
        label = code.value.replace("_", " ").title()
        options.append(f"<option value='{code.value}'>{html.escape(label)}</option>")
    return "".join(options)


def _render_job_detail(
    booking: Booking,
    invoice: Invoice | None,
    summary: time_schemas.TimeTrackingResponse,
    reasons: list,
    *,
    message: str | None = None,
    error: bool = False,
) -> str:
    lead: Lead | None = getattr(booking, "lead", None)
    lead_name = getattr(lead, "name", "Unknown") or "Unknown"
    lead_address = getattr(lead, "address", "On file") or "On file"
    lead_notes = getattr(lead, "notes", "None") or "None"
    scope_bits = []
    if lead and isinstance(lead.structured_inputs, dict):
        for key, value in lead.structured_inputs.items():
            scope_bits.append(f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>")
    addons = getattr(booking, "order_addons", [])
    addon_rows = "".join(
        f"<li>{html.escape(getattr(addon.definition, 'name', f'Addon {addon.addon_id}'))} × {addon.qty}</li>"
        for addon in addons
    ) or "<li>No add-ons planned.</li>"
    evidence_note = booking.risk_reasons or []
    if booking.deposit_required:
        evidence_note.append("Deposit confirmation may be required.")
    invoice_block = "<p class=\"muted\">Invoice not issued.</p>"
    if invoice:
        invoice_block = f"Invoice {html.escape(invoice.invoice_number)} — {html.escape(invoice.status)}"

    planned_minutes = summary.planned_minutes or booking.duration_minutes or 0
    actual_minutes = ceil(summary.effective_seconds / 60) if summary.effective_seconds else 0
    state = summary.state or "NOT_STARTED"
    controls: list[str] = []
    if summary.state is None:
        controls.append(
            f"<form class='inline' method='post' action='/worker/jobs/{booking.booking_id}/start'>"
            f"<button class='button primary' type='submit'>Start</button></form>"
        )
    elif summary.state == time_service.RUNNING:
        controls.append(
            f"<form class='inline' method='post' action='/worker/jobs/{booking.booking_id}/pause'>"
            f"<button class='button secondary' type='submit'>Pause</button></form>"
        )
    elif summary.state == time_service.PAUSED:
        controls.append(
            f"<form class='inline' method='post' action='/worker/jobs/{booking.booking_id}/resume'>"
            f"<button class='button primary' type='submit'>Resume</button></form>"
        )

    finish_form = ""
    if summary.state in {time_service.RUNNING, time_service.PAUSED}:
        finish_form = f"""
        <form method="post" action="/worker/jobs/{booking.booking_id}/finish" class="stack-tight" style="margin-top:8px;">
          <label class="muted">Why did it take longer?</label>
          <select name="delay_reason">{_reason_options(reason_schemas.TIME_OVERRUN_CODES)}</select>
          <textarea name="delay_note" rows="2" placeholder="Optional note"></textarea>
          <label class="muted">Pricing adjustment reason (optional)</label>
          <select name="price_adjust_reason">{_reason_options(reason_schemas.PRICE_ADJUST_CODES)}</select>
          <textarea name="price_adjust_note" rows="2" placeholder="Discount or surcharge note"></textarea>
          <button class="button primary" type="submit">Finish</button>
        </form>
        """
    elif summary.state == time_service.FINISHED:
        controls.append("<span class=\"pill\">Finished</span>")

    reason_rows = "".join(
        f"<li><strong>{html.escape(reason.kind)}</strong>: {html.escape(reason.code)}"
        f" — {html.escape(reason.note or 'No note')}</li>"
        for reason in reasons
    )
    reasons_block = (
        f"<ul class=\"stack-tight\">{reason_rows}</ul>" if reason_rows else "<p class=\"muted\">No reasons captured yet.</p>"
    )

    alert_block = ""
    if message:
        alert_block = f"<div class=\"{'alert error' if error else 'alert'}\">{html.escape(message)}</div>"

    return f"""
    {alert_block}
    <div class="card">
      <div class="row">
        <div class="title">Job {_status_badge(booking.status)}</div>
        {_risk_badge(booking)}
      </div>
      <div class="muted">Starts at {_format_dt(booking.starts_at)} · {booking.duration_minutes} mins</div>
      <div class="muted">Customer: {html.escape(lead_name)}</div>
      <div class="muted">Address: {html.escape(lead_address)}</div>
      <div class="stack" style="margin-top:8px;">
        <div class="pill">Deposit: {'Yes' if booking.deposit_required else 'No'} {_deposit_badge(booking, invoice)}</div>
        <div class="pill">Invoice: {invoice_block}</div>
        <div class="pill">Cancellation: {_cancellation_summary(booking)}</div>
      </div>
    </div>
    <div class="card">
      <h3>Time tracking</h3>
      <div class="stack-tight">
        <div class="muted">Planned: {planned_minutes or '-'} mins · Actual: {actual_minutes} mins</div>
        <div class="muted">State: {html.escape(state)}</div>
        <div class="actions">{''.join(controls) or '<span class=\"muted\">No actions available.</span>'}</div>
      </div>
      {finish_form}
    </div>
    <div class="card">
      <h3>Reasons</h3>
      {reasons_block}
    </div>
    <div class="card">
      <h3>Scope & notes</h3>
      <table>{''.join(scope_bits) or '<tr><td colspan=2 class=\"muted\">No scope captured.</td></tr>'}</table>
      <p class="muted">Customer notes: {html.escape(lead_notes)}</p>
    </div>
    <div class="card">
      <h3>Add-ons planned</h3>
      <ul>{addon_rows}</ul>
    </div>
    <div class="card">
      <h3>Evidence required</h3>
      <p class="muted">{html.escape('; '.join(evidence_note) or 'Standard before/after photos recommended.')}</p>
    </div>
    """


async def _render_job_page(
    session: AsyncSession,
    booking: Booking,
    identity: WorkerIdentity,
    *,
    message: str | None = None,
    error: bool = False,
    log_view: bool = True,
) -> HTMLResponse:
    await addon_service.list_order_addons(session, booking.booking_id)
    await session.refresh(booking, attribute_names=["lead", "order_addons"])
    invoice_map = await _latest_invoices(session, [booking.booking_id])
    invoice = invoice_map.get(booking.booking_id)
    summary_raw = await time_service.fetch_time_tracking_summary(session, booking.booking_id)
    if summary_raw is None:
        summary_raw = time_service.summarize_order_time(booking, None)
    summary = time_schemas.TimeTrackingResponse(**summary_raw)
    reasons = await reason_service.list_reasons_for_order(session, booking.booking_id)
    if log_view:
        await _audit(
            session,
            identity,
            action="VIEW_JOB",
            resource_type="booking",
            resource_id=booking.booking_id,
        )
    body = _render_job_detail(
        booking,
        invoice,
        summary,
        reasons,
        message=message,
        error=error,
    )
    return HTMLResponse(_wrap_page(body, title="Job details", active="jobs"))


async def _audit_transition(
    session: AsyncSession,
    identity: WorkerIdentity,
    booking_id: str,
    *,
    from_state: str | None,
    to_state: str | None,
    reason: str | None = None,
) -> None:
    payload = {
        "job_id": booking_id,
        "from": from_state,
        "to": to_state,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if reason:
        payload["reason"] = reason
    await _audit(
        session,
        identity,
        action="WORKER_TIME_UPDATE",
        resource_type="booking",
        resource_id=booking_id,
        before={"state": from_state},
        after=payload,
    )


@router.post("/worker/login")
async def worker_login(request: Request, identity: WorkerIdentity = Depends(get_worker_identity)) -> JSONResponse:
    token = _session_token(identity.username, identity.role, identity.team_id)
    secure = settings.app_env != "dev" and request.url.scheme == "https"
    response = JSONResponse({"status": "ok"})
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        secure=secure,
        samesite="lax",
    )
    return response


@router.get("/worker", response_class=HTMLResponse)
async def worker_dashboard(
    identity: WorkerIdentity = Depends(require_worker), session: AsyncSession = Depends(get_db_session)
) -> HTMLResponse:
    bookings = await _load_team_bookings(session, identity.team_id)
    counts: dict[str, int] = defaultdict(int)
    for booking in bookings:
        counts[booking.status] += 1
    next_job = None
    now = datetime.now(timezone.utc)
    for booking in bookings:
        starts_at = booking.starts_at
        if starts_at and starts_at.tzinfo is None:
            starts_at = starts_at.replace(tzinfo=timezone.utc)
        if starts_at and starts_at >= now:
            next_job = booking
            break
    await _audit(session, identity, action="VIEW_DASHBOARD", resource_type="portal", resource_id=None)
    return HTMLResponse(_wrap_page(_render_dashboard(next_job, counts), title="Worker dashboard", active="dashboard"))


@router.get("/worker/jobs", response_class=HTMLResponse)
async def worker_jobs(
    identity: WorkerIdentity = Depends(require_worker), session: AsyncSession = Depends(get_db_session)
) -> HTMLResponse:
    bookings = await _load_team_bookings(session, identity.team_id)
    invoices = await _latest_invoices(session, [b.booking_id for b in bookings])
    cards = "".join(_render_job_card(booking, invoices.get(booking.booking_id)) for booking in bookings) or "<p class=\"muted\">No jobs yet.</p>"
    await _audit(session, identity, action="VIEW_JOBS", resource_type="portal", resource_id=None)
    return HTMLResponse(_wrap_page(cards, title="My jobs", active="jobs"))


@router.get("/worker/jobs/{job_id}", response_class=HTMLResponse)
async def worker_job_detail(
    job_id: str,
    identity: WorkerIdentity = Depends(require_worker),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    booking = await _load_worker_booking(session, job_id, identity)
    return await _render_job_page(session, booking, identity)


@router.post("/worker/jobs/{job_id}/start", response_class=HTMLResponse)
async def worker_start_job(
    job_id: str,
    identity: WorkerIdentity = Depends(require_worker),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    booking = await _load_worker_booking(session, job_id, identity)
    before_entry = await time_service.fetch_time_entry(session, booking.booking_id)
    before_state = getattr(before_entry, "state", None)
    message = None
    error = False
    try:
        entry = await time_service.start_time_tracking(
            session, booking_id=booking.booking_id, worker_id=identity.username
        )
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
        after_state = getattr(entry, "state", None)
        if after_state != before_state:
            await _audit_transition(
                session,
                identity,
                booking.booking_id,
                from_state=before_state,
                to_state=after_state,
            )
            await _record_job_event(session, booking, analytics_service.EventType.job_time_started)
        message = "Time tracking started" if after_state != before_state else "Already running"
    except ValueError as exc:
        message = str(exc)
        error = True
    return await _render_job_page(session, booking, identity, message=message, error=error, log_view=False)


@router.post("/worker/jobs/{job_id}/pause", response_class=HTMLResponse)
async def worker_pause_job(
    job_id: str,
    identity: WorkerIdentity = Depends(require_worker),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    booking = await _load_worker_booking(session, job_id, identity)
    entry = await time_service.fetch_time_entry(session, booking.booking_id)
    before_state = getattr(entry, "state", None)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Time tracking not started")
    message = None
    error = False
    try:
        updated = await time_service.pause_time_tracking(
            session, booking_id=booking.booking_id, worker_id=identity.username
        )
        if updated is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
        after_state = getattr(updated, "state", None)
        if after_state != before_state:
            await _audit_transition(
                session,
                identity,
                booking.booking_id,
                from_state=before_state,
                to_state=after_state,
            )
            await _record_job_event(session, booking, analytics_service.EventType.job_time_paused)
        message = "Paused" if after_state != before_state else "Already paused"
    except ValueError as exc:
        message = str(exc)
        error = True
    return await _render_job_page(session, booking, identity, message=message, error=error, log_view=False)


@router.post("/worker/jobs/{job_id}/resume", response_class=HTMLResponse)
async def worker_resume_job(
    job_id: str,
    identity: WorkerIdentity = Depends(require_worker),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    booking = await _load_worker_booking(session, job_id, identity)
    entry = await time_service.fetch_time_entry(session, booking.booking_id)
    before_state = getattr(entry, "state", None)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Time tracking not started")
    message = None
    error = False
    try:
        updated = await time_service.resume_time_tracking(
            session, booking_id=booking.booking_id, worker_id=identity.username
        )
        if updated is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
        after_state = getattr(updated, "state", None)
        if after_state != before_state:
            await _audit_transition(
                session,
                identity,
                booking.booking_id,
                from_state=before_state,
                to_state=after_state,
            )
            await _record_job_event(session, booking, analytics_service.EventType.job_time_resumed)
        message = "Resumed" if after_state != before_state else "Already running"
    except ValueError as exc:
        message = str(exc)
        error = True
    return await _render_job_page(session, booking, identity, message=message, error=error, log_view=False)


@router.post("/worker/jobs/{job_id}/finish", response_class=HTMLResponse)
async def worker_finish_job(
    job_id: str,
    delay_reason: str | None = Form(None),
    delay_note: str | None = Form(None),
    price_adjust_reason: str | None = Form(None),
    price_adjust_note: str | None = Form(None),
    identity: WorkerIdentity = Depends(require_worker),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    booking = await _load_worker_booking(session, job_id, identity)
    entry = await time_service.fetch_time_entry(session, booking.booking_id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Time tracking not started")
    before_state = getattr(entry, "state", None)
    message = None
    error = False
    delay_code: reason_schemas.ReasonCode | None = None
    try:
        if delay_reason:
            try:
                delay_code = reason_schemas.ReasonCode(delay_reason)
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid delay reason") from exc
            await reason_service.create_reason(
                session,
                booking.booking_id,
                kind=reason_schemas.ReasonKind.TIME_OVERRUN,
                code=delay_code,
                note=(delay_note or "").strip() or None,
                created_by=identity.username,
                time_entry_id=getattr(entry, "entry_id", None),
            )

        if price_adjust_reason:
            try:
                price_code = reason_schemas.ReasonCode(price_adjust_reason)
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid price adjust reason") from exc
            await reason_service.create_reason(
                session,
                booking.booking_id,
                kind=reason_schemas.ReasonKind.PRICE_ADJUST,
                code=price_code,
                note=(price_adjust_note or "").strip() or None,
                created_by=identity.username,
                time_entry_id=getattr(entry, "entry_id", None),
            )

        reason_provided = bool(delay_code) or await reason_service.has_reason(
            session,
            booking.booking_id,
            kind=reason_schemas.ReasonKind.TIME_OVERRUN,
            time_entry_id=getattr(entry, "entry_id", None),
        )

        updated = await time_service.finish_time_tracking(
            session,
            booking_id=booking.booking_id,
            reason_provided=reason_provided,
            threshold=settings.time_overrun_reason_threshold,
            worker_id=identity.username,
        )
        if updated is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
        await session.refresh(booking)
        after_state = getattr(updated, "state", None)
        if after_state != before_state:
            await _audit_transition(
                session,
                identity,
                booking.booking_id,
                from_state=before_state,
                to_state=after_state,
                reason=delay_code.value if delay_code else None,
            )
            await _record_job_event(session, booking, analytics_service.EventType.job_time_finished)
        message = "Finished" if after_state != before_state else "Already finished"
    except ValueError as exc:
        message = str(exc)
        error = True
    return await _render_job_page(session, booking, identity, message=message, error=error, log_view=False)

