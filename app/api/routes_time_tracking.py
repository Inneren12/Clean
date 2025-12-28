import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes_admin import require_admin, verify_admin_or_dispatcher
from app.dependencies import get_db_session
from app.domain.bookings.db_models import Booking
from app.domain.time_tracking import schemas as time_schemas
from app.domain.time_tracking import service as time_service

router = APIRouter()
logger = logging.getLogger(__name__)


async def _ensure_booking(session: AsyncSession, booking_id: str) -> Booking:
    booking = await session.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return booking


def _serialize_summary(summary: dict[str, object]) -> time_schemas.TimeTrackingResponse:
    return time_schemas.TimeTrackingResponse(**summary)


@router.post(
    "/v1/orders/{booking_id}/time/start",
    response_model=time_schemas.TimeTrackingResponse,
    status_code=status.HTTP_200_OK,
)
async def start_order_time_tracking(
    booking_id: str,
    session: AsyncSession = Depends(get_db_session),
    identity=Depends(verify_admin_or_dispatcher),
) -> time_schemas.TimeTrackingResponse:
    await _ensure_booking(session, booking_id)
    try:
        entry = await time_service.start_time_tracking(
            session, booking_id=booking_id, worker_id=identity.username
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    summary = await time_service.fetch_time_tracking_summary(session, booking_id)
    return _serialize_summary(summary)


@router.post(
    "/v1/orders/{booking_id}/time/pause",
    response_model=time_schemas.TimeTrackingResponse,
    status_code=status.HTTP_200_OK,
)
async def pause_order_time_tracking(
    booking_id: str,
    session: AsyncSession = Depends(get_db_session),
    identity=Depends(verify_admin_or_dispatcher),
) -> time_schemas.TimeTrackingResponse:
    await _ensure_booking(session, booking_id)
    try:
        entry = await time_service.pause_time_tracking(session, booking_id=booking_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    summary = await time_service.fetch_time_tracking_summary(session, booking_id)
    return _serialize_summary(summary)


@router.post(
    "/v1/orders/{booking_id}/time/resume",
    response_model=time_schemas.TimeTrackingResponse,
    status_code=status.HTTP_200_OK,
)
async def resume_order_time_tracking(
    booking_id: str,
    session: AsyncSession = Depends(get_db_session),
    identity=Depends(verify_admin_or_dispatcher),
) -> time_schemas.TimeTrackingResponse:
    await _ensure_booking(session, booking_id)
    try:
        entry = await time_service.resume_time_tracking(session, booking_id=booking_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    summary = await time_service.fetch_time_tracking_summary(session, booking_id)
    return _serialize_summary(summary)


@router.post(
    "/v1/orders/{booking_id}/time/finish",
    response_model=time_schemas.TimeTrackingResponse,
    status_code=status.HTTP_200_OK,
)
async def finish_order_time_tracking(
    booking_id: str,
    session: AsyncSession = Depends(get_db_session),
    identity=Depends(verify_admin_or_dispatcher),
) -> time_schemas.TimeTrackingResponse:
    await _ensure_booking(session, booking_id)
    try:
        entry = await time_service.finish_time_tracking(session, booking_id=booking_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    summary = await time_service.fetch_time_tracking_summary(session, booking_id)
    return _serialize_summary(summary)


@router.get(
    "/v1/orders/{booking_id}/time",
    response_model=time_schemas.TimeTrackingResponse,
    status_code=status.HTTP_200_OK,
)
async def get_order_time_tracking(
    booking_id: str,
    session: AsyncSession = Depends(get_db_session),
    identity=Depends(verify_admin_or_dispatcher),
) -> time_schemas.TimeTrackingResponse:
    await _ensure_booking(session, booking_id)
    summary = await time_service.fetch_time_tracking_summary(session, booking_id)
    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return _serialize_summary(summary)


@router.get(
    "/v1/admin/orders/time",
    response_model=list[time_schemas.TimeTrackingResponse],
    status_code=status.HTTP_200_OK,
)
async def list_orders_time_tracking(
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),
    role=Depends(require_admin),
) -> list[time_schemas.TimeTrackingResponse]:
    stmt = select(Booking.booking_id).order_by(Booking.starts_at.desc()).limit(limit)
    result = await session.execute(stmt)
    booking_ids = [row[0] for row in result.all()]
    summaries = await time_service.list_time_tracking_summaries(session, booking_ids)
    return [_serialize_summary(summary) for summary in summaries]
