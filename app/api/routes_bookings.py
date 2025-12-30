from datetime import timedelta

import logging

from fastapi import APIRouter, Depends, Request, status
from fastapi.exceptions import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_auth import require_admin
from app.domain.analytics.service import (
    EventType,
    estimated_duration_from_booking,
    estimated_revenue_from_lead,
    log_event,
)
from app.domain.bookings.db_models import Booking, TeamBlackout, TeamWorkingHours
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
    session: AsyncSession = Depends(get_db_session), role: str = Depends(require_admin)
) -> list[booking_schemas.WorkingHoursResponse]:
    del role
    result = await session.execute(select(TeamWorkingHours))
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
    session: AsyncSession = Depends(get_db_session),
    role: str = Depends(require_admin),
) -> booking_schemas.WorkingHoursResponse:
    del role
    existing_result = await session.execute(
        select(TeamWorkingHours).where(
            TeamWorkingHours.team_id == payload.team_id,
            TeamWorkingHours.day_of_week == payload.day_of_week,
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
    session: AsyncSession = Depends(get_db_session), role: str = Depends(require_admin)
) -> list[booking_schemas.BlackoutResponse]:
    del role
    result = await session.execute(select(TeamBlackout))
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
    session: AsyncSession = Depends(get_db_session),
    role: str = Depends(require_admin),
) -> booking_schemas.BlackoutResponse:
    del role
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
    session: AsyncSession = Depends(get_db_session),
    role: str = Depends(require_admin),
) -> None:
    del role
    blackout = await session.get(TeamBlackout, blackout_id)
    if blackout:
        await session.delete(blackout)
        await session.commit()
    return None


@router.post("/v1/bookings", response_model=booking_schemas.BookingResponse, status_code=status.HTTP_201_CREATED)
async def create_booking(
    request: booking_schemas.BookingCreateRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> booking_schemas.BookingResponse:
    start = request.normalized_start()
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
                    checkout_session = stripe_infra.create_checkout_session(
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


@router.post("/v1/stripe/webhook", status_code=status.HTTP_200_OK)
async def stripe_webhook(http_request: Request, session: AsyncSession = Depends(get_db_session)) -> dict[str, bool]:
    payload = await http_request.body()
    sig_header = http_request.headers.get("Stripe-Signature")
    if not settings.stripe_webhook_secret:
        logger.warning(
            "booking_dependency_unavailable",
            extra={
                "extra": {
                    "dependency": "stripe_webhook",
                    "path": http_request.url.path,
                    "method": http_request.method,
                }
            },
        )
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Stripe webhook disabled")

    stripe_client = stripe_infra.resolve_client(http_request.app.state)
    try:
        event = stripe_infra.parse_webhook_event(
            stripe_client=stripe_client,
            payload=payload,
            signature=sig_header,
            webhook_secret=settings.stripe_webhook_secret,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("stripe_webhook_invalid", extra={"extra": {"reason": type(exc).__name__}})
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Stripe webhook") from exc

    def _safe_get(source: object, key: str, default: object | None = None):
        if isinstance(source, dict):
            return source.get(key, default)
        return getattr(source, key, default)

    event_type = _safe_get(event, "type")
    data = _safe_get(event, "data", {}) or {}
    payload_object = _safe_get(data, "object", {}) or {}
    session_id = _safe_get(payload_object, "id")
    payment_intent_id = _safe_get(payload_object, "payment_intent") or _safe_get(payload_object, "id")
    payment_status = _safe_get(payload_object, "payment_status")

    if event_type == "checkout.session.completed" and payment_status == "paid":
        await booking_service.mark_deposit_paid(
            session=session,
            checkout_session_id=session_id,
            payment_intent_id=payment_intent_id,
            email_adapter=getattr(http_request.app.state, "email_adapter", None),
        )
    elif event_type in {"checkout.session.expired", "payment_intent.payment_failed"}:
        await booking_service.mark_deposit_failed(
            session=session,
            checkout_session_id=session_id,
            payment_intent_id=payment_intent_id,
            failure_status="expired" if event_type == "checkout.session.expired" else "failed",
        )

    return {"received": True}
