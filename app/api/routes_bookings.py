from datetime import timedelta

import logging

from fastapi import APIRouter, Depends, Request, status
from fastapi.exceptions import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_auth import require_admin
from app.api import entitlements
from app.domain.saas import billing_service
from app.domain.analytics.service import (
    EventType,
    estimated_duration_from_booking,
    estimated_revenue_from_lead,
    log_event,
)
from app.domain.bookings.db_models import Booking, Team, TeamBlackout, TeamWorkingHours
from app.dependencies import get_db_session
from app.domain.bookings import schemas as booking_schemas
from app.domain.bookings import service as booking_service
from app.domain.leads.db_models import Lead
from app.domain.clients import service as client_service
from app.domain.notifications import email_service
from app.infra import stripe as stripe_infra
from app.settings import settings

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/v1/slots", response_model=booking_schemas.SlotAvailabilityResponse)
async def get_slots(
    query: booking_schemas.SlotQuery = Depends(),
    session: AsyncSession = Depends(get_db_session),
) -> booking_schemas.SlotAvailabilityResponse:
    slot_result = await booking_service.suggest_slots(
        query.date,
        query.duration_minutes,
        session,
        time_window=query.time_window(),
        service_type=query.service_type.value if query.service_type else None,
    )
    return booking_schemas.SlotAvailabilityResponse(
        date=query.date,
        duration_minutes=query.duration_minutes,
        slots=slot_result.slots,
        clarifier=slot_result.clarifier,
    )


@router.get(
    "/v1/admin/working-hours",
    response_model=list[booking_schemas.WorkingHoursResponse],
)
async def list_working_hours(
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
    role: str = Depends(require_admin),
) -> list[booking_schemas.WorkingHoursResponse]:
    del role
    org_id = entitlements.resolve_org_id(http_request)
    result = await session.execute(
        select(TeamWorkingHours)
        .join(Team, Team.team_id == TeamWorkingHours.team_id)
        .where(Team.org_id == org_id)
    )
    records = result.scalars().all()
    return [
        booking_schemas.WorkingHoursResponse(
            id=record.id,
            team_id=record.team_id,
            day_of_week=record.day_of_week,
            start_time=record.start_time,
            end_time=record.end_time,
        )
        for record in records
    ]


@router.post(
    "/v1/admin/working-hours",
    response_model=booking_schemas.WorkingHoursResponse,
)
async def upsert_working_hours(
    payload: booking_schemas.WorkingHoursUpdateRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
    role: str = Depends(require_admin),
) -> booking_schemas.WorkingHoursResponse:
    del role
    org_id = entitlements.resolve_org_id(http_request)
    team = (
        await session.execute(
            select(Team).where(Team.team_id == payload.team_id, Team.org_id == org_id)
        )
    ).scalar_one_or_none()
    if team is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    existing_result = await session.execute(
        select(TeamWorkingHours)
        .join(Team, Team.team_id == TeamWorkingHours.team_id)
        .where(
            TeamWorkingHours.team_id == payload.team_id,
            TeamWorkingHours.day_of_week == payload.day_of_week,
            Team.org_id == org_id,
        )
    )
    record = existing_result.scalar_one_or_none()
    if record:
        record.start_time = payload.start_time
        record.end_time = payload.end_time
    else:
        record = TeamWorkingHours(
            team_id=payload.team_id,
            day_of_week=payload.day_of_week,
            start_time=payload.start_time,
            end_time=payload.end_time,
        )
        session.add(record)

    await session.commit()
    await session.refresh(record)
    return booking_schemas.WorkingHoursResponse(
        id=record.id,
        team_id=record.team_id,
        day_of_week=record.day_of_week,
        start_time=record.start_time,
        end_time=record.end_time,
    )


@router.get(
    "/v1/admin/blackouts", response_model=list[booking_schemas.BlackoutResponse]
)
async def list_blackouts(
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
    role: str = Depends(require_admin),
) -> list[booking_schemas.BlackoutResponse]:
    del role
    org_id = entitlements.resolve_org_id(http_request)
    result = await session.execute(
        select(TeamBlackout)
        .join(Team, Team.team_id == TeamBlackout.team_id)
        .where(Team.org_id == org_id)
    )
    records = result.scalars().all()
    return [
        booking_schemas.BlackoutResponse(
            id=record.id,
            team_id=record.team_id,
            starts_at=record.starts_at,
            ends_at=record.ends_at,
            reason=record.reason,
        )
        for record in records
    ]


@router.post(
    "/v1/admin/blackouts", response_model=booking_schemas.BlackoutResponse
)
async def create_blackout(
    payload: booking_schemas.BlackoutCreateRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
    role: str = Depends(require_admin),
) -> booking_schemas.BlackoutResponse:
    del role
    org_id = entitlements.resolve_org_id(http_request)
    team = (
        await session.execute(
            select(Team).where(Team.team_id == payload.team_id, Team.org_id == org_id)
        )
    ).scalar_one_or_none()
    if team is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    blackout = TeamBlackout(
        team_id=payload.team_id,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        reason=payload.reason,
    )
    session.add(blackout)
    await session.commit()
    await session.refresh(blackout)
    return booking_schemas.BlackoutResponse(
        id=blackout.id,
        team_id=blackout.team_id,
        starts_at=blackout.starts_at,
        ends_at=blackout.ends_at,
        reason=blackout.reason,
    )


@router.delete("/v1/admin/blackouts/{blackout_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_blackout(
    blackout_id: int,
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
    role: str = Depends(require_admin),
) -> None:
    del role
    org_id = entitlements.resolve_org_id(http_request)
    blackout = (
        await session.execute(
            select(TeamBlackout)
            .join(Team, Team.team_id == TeamBlackout.team_id)
            .where(TeamBlackout.id == blackout_id, Team.org_id == org_id)
        )
    ).scalar_one_or_none()
    if blackout:
        await session.delete(blackout)
        await session.commit()
    else:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Blackout not found")
    return None


@router.post("/v1/bookings", response_model=booking_schemas.BookingResponse, status_code=status.HTTP_201_CREATED)
async def create_booking(
    request: booking_schemas.BookingCreateRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> booking_schemas.BookingResponse:
    start = request.normalized_start()
    org_id = entitlements.resolve_org_id(http_request)
    await entitlements.require_booking_entitlement(http_request, session=session)
    lead: Lead | None = None
    if request.lead_id:
        lead = await session.get(Lead, request.lead_id)

    client_id: str | None = None
    if lead and lead.email:
        client_user = await client_service.get_or_create_client(
            session, lead.email, name=lead.name, commit=False
        )
        client_id = client_user.client_id

    risk_assessment = await booking_service.evaluate_risk(
        session=session,
        lead=lead,
        client_id=client_id,
        starts_at=start,
        postal_code=lead.postal_code if lead else None,
    )

    deposit_decision = await booking_service.evaluate_deposit_policy(
        session=session,
        lead=lead,
        starts_at=start,
        deposit_percent=settings.deposit_percent,
        deposits_enabled=settings.deposits_enabled,
        service_type=request.service_type.value if request.service_type else None,
        force_deposit=risk_assessment.requires_deposit,
        extra_reasons=[f"risk_{risk_assessment.band.value.lower()}"]
        if risk_assessment.requires_deposit
        else None,
    )
    if deposit_decision.required and deposit_decision.deposit_cents is None:
        deposit_decision = booking_service.downgrade_deposit_requirement(
            deposit_decision, reason="deposit_estimate_unavailable"
        )
    if deposit_decision.required and not settings.stripe_secret_key:
        deposit_decision = booking_service.downgrade_deposit_requirement(
            deposit_decision, reason="stripe_unavailable"
        )

    checkout_url: str | None = None
    email_adapter = getattr(http_request.app.state, "email_adapter", None)
    booking: Booking | None = None

    try:
        transaction_ctx = session.begin_nested() if session.in_transaction() else session.begin()
        async with transaction_ctx:
            booking = await booking_service.create_booking(
                starts_at=start,
                duration_minutes=request.duration_minutes,
                lead_id=request.lead_id,
                session=session,
                org_id=org_id,
                deposit_decision=deposit_decision,
                policy_snapshot=deposit_decision.policy_snapshot,
                risk_assessment=risk_assessment,
                manage_transaction=False,
                client_id=client_id,
                lead=lead,
                service_type=request.service_type,
            )

            try:
                if booking.lead_id and lead:
                    await log_event(
                        session,
                        event_type=EventType.booking_created,
                        booking=booking,
                        lead=lead,
                        estimated_revenue_cents=estimated_revenue_from_lead(lead),
                        estimated_duration_minutes=estimated_duration_from_booking(booking),
                    )
                else:
                    await log_event(
                        session,
                        event_type=EventType.booking_created,
                        booking=booking,
                        estimated_duration_minutes=estimated_duration_from_booking(booking),
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "analytics_log_failed",
                    extra={
                        "extra": {
                            "event_type": "booking_created",
                            "booking_id": booking.booking_id,
                            "lead_id": booking.lead_id,
                            "reason": type(exc).__name__,
                        }
                    },
                )

            if deposit_decision.required and deposit_decision.deposit_cents:
                stripe_client = stripe_infra.resolve_client(http_request.app.state)
                metadata = {"booking_id": booking.booking_id}
                if booking.lead_id:
                    metadata["lead_id"] = booking.lead_id
                try:
                    checkout_session = await stripe_infra.create_checkout_session(
                        stripe_client=stripe_client,
                        secret_key=settings.stripe_secret_key,
                        amount_cents=deposit_decision.deposit_cents,
                        currency=settings.deposit_currency,
                        success_url=settings.stripe_success_url.replace("{BOOKING_ID}", booking.booking_id),
                        cancel_url=settings.stripe_cancel_url.replace("{BOOKING_ID}", booking.booking_id),
                        metadata=metadata,
                    )
                    checkout_url = getattr(checkout_session, "url", None) or checkout_session.get("url")
                    payment_intent = getattr(checkout_session, "payment_intent", None) or checkout_session.get("payment_intent")
                    await booking_service.attach_checkout_session(
                        session,
                        booking.booking_id,
                        checkout_session.id,
                        payment_intent_id=payment_intent,
                        commit=False,
                    )
                except Exception as exc:  # noqa: BLE001
                    deposit_decision = booking_service.downgrade_deposit_requirement(
                        deposit_decision, reason="checkout_unavailable"
                    )
                    booking.deposit_required = False
                    booking.deposit_status = None
                    booking.deposit_policy = list(deposit_decision.reasons)
                    booking.deposit_cents = None
                    booking.policy_snapshot = deposit_decision.policy_snapshot.model_dump(mode="json")
                    await session.flush()
                    logger.warning(
                        "stripe_checkout_creation_failed",
                        extra={
                            "extra": {
                                "event": "policy_downgraded",
                                "booking_id": booking.booking_id,
                                "lead_id": booking.lead_id,
                                "reason": type(exc).__name__,
                            }
                        },
                    )

        if booking is not None:
            await session.refresh(booking)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    if booking.lead_id and lead:
        try:
            await email_service.send_booking_pending_email(session, email_adapter, booking, lead)
        except Exception:  # noqa: BLE001
            logger.warning(
                "booking_pending_email_failed",
                extra={"extra": {"booking_id": booking.booking_id, "lead_id": booking.lead_id}},
            )

    if entitlements.has_tenant_identity(http_request):
        await billing_service.record_usage_event(
            session,
            org_id,
            metric="booking_created",
            quantity=1,
            resource_id=booking.booking_id,
        )
    await session.commit()

    return booking_schemas.BookingResponse(
        booking_id=booking.booking_id,
        status=booking.status,
        starts_at=booking.starts_at,
        duration_minutes=booking.duration_minutes,
        deposit_required=booking.deposit_required,
        deposit_cents=booking.deposit_cents,
        deposit_policy=booking.deposit_policy,
        deposit_status=booking.deposit_status,
        checkout_url=checkout_url,
        policy_snapshot=booking.policy_snapshot,
        risk_score=booking.risk_score,
        risk_band=booking.risk_band,
        risk_reasons=booking.risk_reasons,
        cancellation_exception=booking.cancellation_exception,
        cancellation_exception_note=booking.cancellation_exception_note,
    )


@router.post("/v1/admin/cleanup", status_code=status.HTTP_202_ACCEPTED)
async def cleanup_pending_bookings(
    session: AsyncSession = Depends(get_db_session),
    role: str = Depends(require_admin),
) -> dict[str, int]:
    deleted = await booking_service.cleanup_stale_bookings(session, timedelta(minutes=30))
    return {"deleted": deleted}


