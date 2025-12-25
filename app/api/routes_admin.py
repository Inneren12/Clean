import secrets
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db_session
from app.domain.bookings.db_models import Booking
from app.domain.leads.db_models import Lead
from app.domain.leads.schemas import AdminLeadResponse
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
    status_filter: Optional[str] = Query(default="NEW", alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(verify_admin),
) -> List[AdminLeadResponse]:
    stmt = select(Lead).order_by(Lead.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    leads = result.scalars().all()
    response: List[AdminLeadResponse] = []
    for lead in leads:
        response.append(
            AdminLeadResponse(
                lead_id=lead.lead_id,
                name=lead.name,
                email=lead.email,
                phone=lead.phone,
                postal_code=lead.postal_code,
                preferred_dates=lead.preferred_dates,
                notes=lead.notes,
                created_at=lead.created_at.isoformat(),
                referrer=lead.referrer,
            )
        )
    return response


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
