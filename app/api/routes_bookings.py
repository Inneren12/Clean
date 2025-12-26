from datetime import timedelta

import logging

from fastapi import APIRouter, Depends, Request, status
from fastapi.exceptions import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes_admin import verify_admin
from app.domain.analytics.service import (
    EventType,
    estimated_duration_from_booking,
    estimated_revenue_from_lead,
    log_event,
)
from app.dependencies import get_db_session
from app.domain.bookings import schemas as booking_schemas
from app.domain.bookings import service as booking_service
from app.domain.leads.db_models import Lead
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
    start = request.normalized_start()
    lead: Lead | None = None
    if request.lead_id:
        lead = await session.get(Lead, request.lead_id)

    deposit_decision = await booking_service.evaluate_deposit_policy(
        session=session,
        lead=lead,
        starts_at=start,
        deposit_percent=settings.deposit_percent,
    )

    if deposit_decision.required and deposit_decision.deposit_cents:
        if not settings.stripe_secret_key:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Deposits unavailable")

    checkout_url: str | None = None
    email_adapter = getattr(http_request.app.state, "email_adapter", None)

    try:
        transaction_ctx = session.begin_nested() if session.in_transaction() else session.begin()
        async with transaction_ctx:
            booking = await booking_service.create_booking(
                starts_at=start,
                duration_minutes=request.duration_minutes,
                lead_id=request.lead_id,
                session=session,
                deposit_decision=deposit_decision,
                manage_transaction=False,
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
                    logger.warning(
                        "stripe_checkout_creation_failed",
                        extra={
                            "extra": {
                                "booking_id": booking.booking_id,
                                "lead_id": booking.lead_id,
                                "reason": type(exc).__name__,
                            }
                        },
                    )
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Failed to create deposit session",
                    ) from exc

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
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    if booking.lead_id and lead:
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
        deposit_required=booking.deposit_required,
        deposit_cents=booking.deposit_cents,
        deposit_policy=booking.deposit_policy,
        deposit_status=booking.deposit_status,
        checkout_url=checkout_url,
    )


@router.post("/v1/admin/cleanup", status_code=status.HTTP_202_ACCEPTED)
async def cleanup_pending_bookings(
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(verify_admin),
) -> dict[str, int]:
    deleted = await booking_service.cleanup_stale_bookings(session, timedelta(minutes=30))
    return {"deleted": deleted}


@router.post("/v1/stripe/webhook", status_code=status.HTTP_200_OK)
async def stripe_webhook(http_request: Request, session: AsyncSession = Depends(get_db_session)) -> dict[str, bool]:
    payload = await http_request.body()
    sig_header = http_request.headers.get("Stripe-Signature")
    if not settings.stripe_webhook_secret:
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

    event_type = event.get("type")
    payload_object = event.get("data", {}).get("object", {})
    session_id = payload_object.get("id")
    payment_intent_id = payload_object.get("payment_intent") or payload_object.get("id")

    if event_type == "checkout.session.completed" and payload_object.get("payment_status") == "paid":
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
