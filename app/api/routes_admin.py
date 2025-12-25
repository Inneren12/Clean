import secrets
from typing import List, Optional

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.domain.leads.db_models import Lead
from app.domain.leads.schemas import AdminLeadResponse, AdminLeadStatusUpdateRequest, admin_lead_from_model
from app.domain.leads.statuses import assert_valid_transition, is_valid_status
from app.domain.notifications import email_service
from app.settings import settings

router = APIRouter()
security = HTTPBasic()


async def verify_admin(credentials: HTTPBasicCredentials = Depends(security)) -> None:
    username = settings.admin_basic_username
    password = settings.admin_basic_password
    if not username or not password:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Admin access not configured")
    if not (
        secrets.compare_digest(credentials.username, username)
        and secrets.compare_digest(credentials.password, password)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication",
            headers={"WWW-Authenticate": "Basic"},
        )


@router.get("/v1/admin/leads", response_model=List[AdminLeadResponse])
async def list_leads(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(verify_admin),
) -> List[AdminLeadResponse]:
    stmt = select(Lead).order_by(Lead.created_at.desc()).limit(limit)
    if status_filter:
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
    _: None = Depends(verify_admin),
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
    return admin_lead_from_model(lead)


@router.post("/v1/admin/email-scan", status_code=status.HTTP_202_ACCEPTED)
async def email_scan(
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(verify_admin),
) -> dict[str, int]:
    adapter = getattr(http_request.app.state, "email_adapter", None)
    result = await email_service.scan_and_send_reminders(session, adapter)
    return result


@router.post("/v1/admin/bookings/{booking_id}/resend-last-email", status_code=status.HTTP_202_ACCEPTED)
async def resend_last_email(
    booking_id: str,
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(verify_admin),
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
    _: None = Depends(verify_admin),
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


@router.post("/v1/admin/bookings/{booking_id}/confirm", response_model=booking_schemas.BookingResponse)
async def confirm_booking(
    booking_id: str,
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(verify_admin),
):
    booking = await session.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

    lead = await session.get(Lead, booking.lead_id) if booking.lead_id else None
    if booking.status != "CONFIRMED":
        booking.status = "CONFIRMED"
        await log_event(
            session,
            event_type=EventType.booking_confirmed,
            booking=booking,
            lead=lead,
            estimated_revenue_cents=estimated_revenue_from_lead(lead),
            estimated_duration_minutes=estimated_duration_from_booking(booking),
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


@router.post(
    "/v1/admin/bookings/{booking_id}/complete",
    response_model=booking_schemas.BookingResponse,
)
async def complete_booking(
    booking_id: str,
    request: booking_schemas.BookingCompletionRequest,
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(verify_admin),
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
