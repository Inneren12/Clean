import secrets
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from collections import defaultdict

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db_session
from app.domain.bookings.db_models import Booking
from app.domain.leads.db_models import Lead, ReferralRedemption
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
    if not leads:
        return []

    lead_ids = [lead.lead_id for lead in leads]
    code_lookup = {lead.lead_id: lead.referral_code for lead in leads}

    redemptions_result = await session.execute(
        select(ReferralRedemption).where(
            or_(
                ReferralRedemption.referrer_lead_id.in_(lead_ids),
                ReferralRedemption.referred_lead_id.in_(lead_ids),
            )
        )
    )
    redemptions = redemptions_result.scalars().all()

    referred_map = {redemption.referred_lead_id: redemption for redemption in redemptions}
    earned_totals: dict[str, int] = defaultdict(int)
    earned_counts: dict[str, int] = defaultdict(int)
    for redemption in redemptions:
        earned_totals[redemption.referrer_lead_id] += redemption.credit_cents
        earned_counts[redemption.referrer_lead_id] += 1

    responses = []
    for lead in leads:
        redemption = referred_map.get(lead.lead_id)
        referred_by_code = code_lookup.get(redemption.referrer_lead_id) if redemption else None
        responses.append(
            admin_lead_from_model(
                lead,
                referred_by_code=referred_by_code,
                referral_credits_cents=earned_totals.get(lead.lead_id, 0),
                referral_redemptions_count=earned_counts.get(lead.lead_id, 0),
            )
        )
    return responses


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
