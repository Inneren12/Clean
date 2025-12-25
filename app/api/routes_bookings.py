from datetime import timedelta

import logging

from fastapi import APIRouter, Depends, Request, status
from fastapi.exceptions import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes_admin import verify_admin
from app.dependencies import get_db_session
from app.domain.bookings import schemas as booking_schemas
from app.domain.bookings import service as booking_service
from app.domain.leads.db_models import Lead
from app.domain.notifications import email_service

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/v1/slots", response_model=booking_schemas.SlotAvailabilityResponse)
async def get_slots(
    query: booking_schemas.SlotQuery = Depends(),
    session: AsyncSession = Depends(get_db_session),
) -> booking_schemas.SlotAvailabilityResponse:
    slots = await booking_service.generate_slots(query.date, query.duration_minutes, session)
    return booking_schemas.SlotAvailabilityResponse(
        date=query.date,
        duration_minutes=query.duration_minutes,
        slots=slots,
    )


@router.post("/v1/bookings", response_model=booking_schemas.BookingResponse, status_code=status.HTTP_201_CREATED)
async def create_booking(
    request: booking_schemas.BookingCreateRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> booking_schemas.BookingResponse:
    try:
        booking = await booking_service.create_booking(
            starts_at=request.normalized_start(),
            duration_minutes=request.duration_minutes,
            lead_id=request.lead_id,
            session=session,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    email_adapter = getattr(http_request.app.state, "email_adapter", None)
    if booking.lead_id:
        lead = await session.get(Lead, booking.lead_id)
        if lead:
            try:
                await email_service.send_booking_pending_email(session, email_adapter, booking, lead)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "booking_pending_email_failed",
                    extra={"extra": {"booking_id": booking.booking_id, "lead_id": booking.lead_id}},
                )

    return booking_schemas.BookingResponse(
        booking_id=booking.booking_id,
        status=booking.status,
        starts_at=booking.starts_at,
        duration_minutes=booking.duration_minutes,
    )


@router.post("/v1/admin/cleanup", status_code=status.HTTP_202_ACCEPTED)
async def cleanup_pending_bookings(
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(verify_admin),
) -> dict[str, int]:
    deleted = await booking_service.cleanup_stale_bookings(session, timedelta(minutes=30))
    return {"deleted": deleted}
