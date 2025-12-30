from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.bookings import service as booking_service
from app.domain.bookings.db_models import Booking
from app.domain.invoices import schemas as invoice_schemas, service as invoice_service, statuses as invoice_statuses
from app.domain.invoices.db_models import Invoice, StripeEvent
from app.infra import stripe_client as stripe_infra
from app.infra.db import get_db_session
from app.settings import settings

router = APIRouter()
logger = logging.getLogger(__name__)


def _safe_get(source: object, key: str, default: Any | None = None) -> Any:
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


async def _lock_invoice(session: AsyncSession, invoice_id: str) -> Invoice | None:
    stmt = select(Invoice).where(Invoice.invoice_id == invoice_id).with_for_update()
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def _lock_booking(session: AsyncSession, booking_id: str) -> Booking | None:
    stmt = select(Booking).where(Booking.booking_id == booking_id).with_for_update()
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def _handle_invoice_event(session: AsyncSession, event: Any) -> bool:
    event_type = _safe_get(event, "type")
    data = _safe_get(event, "data", {}) or {}
    payload_object = _safe_get(data, "object", {}) or {}
    metadata = _safe_get(payload_object, "metadata", {}) or {}
    invoice_id = metadata.get("invoice_id") if isinstance(metadata, dict) else None

    if not invoice_id:
        logger.info(
            "stripe_invoice_event_ignored",
            extra={"extra": {"reason": "missing_invoice_metadata", "event_type": event_type}},
        )
        return False

    invoice = await _lock_invoice(session, invoice_id)
    if invoice is None:
        logger.info(
            "stripe_invoice_event_ignored",
            extra={"extra": {"reason": "invoice_not_found", "invoice_id": invoice_id}},
        )
        return False

    payment_status = None
    provider_ref = None
    checkout_session_id = None
    if event_type == "checkout.session.completed":
        provider_ref = _safe_get(payload_object, "payment_intent")
        checkout_session_id = _safe_get(payload_object, "id")
        if not provider_ref:
            logger.info(
                "stripe_invoice_event_ignored",
                extra={"extra": {"reason": "missing_payment_intent", "event_type": event_type}},
            )
            return False
        payment_status = (
            invoice_statuses.PAYMENT_STATUS_SUCCEEDED
            if _safe_get(payload_object, "payment_status") == "paid"
            else None
        )
    elif event_type == "payment_intent.succeeded":
        provider_ref = _safe_get(payload_object, "id")
        payment_status = invoice_statuses.PAYMENT_STATUS_SUCCEEDED
    elif event_type == "payment_intent.payment_failed":
        provider_ref = _safe_get(payload_object, "id")
        payment_status = invoice_statuses.PAYMENT_STATUS_FAILED

    if payment_status is None:
        logger.info(
            "stripe_invoice_event_ignored",
            extra={"extra": {"reason": "unsupported_event", "event_type": event_type}},
        )
        return False

    amount_cents = (
        _safe_get(payload_object, "amount_received")
        or _safe_get(payload_object, "amount_total")
        or _safe_get(payload_object, "amount")
        or 0
    )
    currency = _safe_get(payload_object, "currency") or invoice.currency
    reference = _safe_get(payload_object, "latest_charge") or provider_ref
    received_at = datetime.fromtimestamp(_safe_get(event, "created", int(time.time())), tz=timezone.utc)

    payment = await invoice_service.record_stripe_payment(
        session=session,
        invoice=invoice,
        amount_cents=int(amount_cents),
        currency=str(currency),
        status=payment_status,
        provider_ref=str(provider_ref) if provider_ref else None,
        reference=str(reference) if reference else None,
        received_at=received_at,
        checkout_session_id=checkout_session_id,
        payment_intent_id=str(provider_ref) if provider_ref else None,
    )
    if payment is None:
        logger.info(
            "stripe_invoice_payment_duplicate",
            extra={"extra": {"invoice_id": invoice.invoice_id, "provider_ref": provider_ref}},
        )
        return False

    logger.info(
        "stripe_invoice_payment_recorded",
        extra={
            "extra": {
                "invoice_id": invoice.invoice_id,
                "payment_id": payment.payment_id,
                "amount_cents": payment.amount_cents,
                "status": payment.status,
            }
        },
    )
    return True


async def _handle_deposit_event(session: AsyncSession, event: Any, email_adapter: Any) -> bool:
    event_type = _safe_get(event, "type")
    data = _safe_get(event, "data", {}) or {}
    payload_object = _safe_get(data, "object", {}) or {}
    metadata = _safe_get(payload_object, "metadata", {}) or {}
    booking_id = metadata.get("booking_id") if isinstance(metadata, dict) else None

    if not booking_id:
        logger.info(
            "stripe_deposit_event_ignored",
            extra={"extra": {"reason": "missing_booking_metadata", "event_type": event_type}},
        )
        return False

    booking = await _lock_booking(session, booking_id)
    if booking is None:
        logger.info(
            "stripe_deposit_event_ignored",
            extra={"extra": {"reason": "booking_not_found", "booking_id": booking_id}},
        )
        return False

    is_checkout_event = event_type.startswith("checkout.session.")
    is_payment_intent_event = event_type.startswith("payment_intent.")

    checkout_session_id = _safe_get(payload_object, "id") if is_checkout_event else None
    payment_intent_id = (
        _safe_get(payload_object, "payment_intent")
        if is_checkout_event
        else _safe_get(payload_object, "id")
    )

    payment_status = None
    failure_status = None
    if is_checkout_event and event_type == "checkout.session.completed" and _safe_get(payload_object, "payment_status") == "paid":
        payment_status = invoice_statuses.PAYMENT_STATUS_SUCCEEDED
    elif is_checkout_event and event_type == "checkout.session.expired":
        payment_status = invoice_statuses.PAYMENT_STATUS_FAILED
        failure_status = "expired"
    elif is_payment_intent_event and event_type == "payment_intent.succeeded":
        payment_status = invoice_statuses.PAYMENT_STATUS_SUCCEEDED
    elif is_payment_intent_event and event_type == "payment_intent.payment_failed":
        payment_status = invoice_statuses.PAYMENT_STATUS_FAILED
        failure_status = "failed"

    if payment_status is None:
        logger.info(
            "stripe_deposit_event_ignored",
            extra={"extra": {"reason": "unsupported_event", "event_type": event_type}},
        )
        return False

    amount_cents = (
        _safe_get(payload_object, "amount_total")
        or _safe_get(payload_object, "amount_received")
        or booking.deposit_cents
        or 0
    )
    currency = _safe_get(payload_object, "currency") or settings.deposit_currency
    received_at = datetime.fromtimestamp(_safe_get(event, "created", int(time.time())), tz=timezone.utc)

    if checkout_session_id:
        await booking_service.attach_checkout_session(
            session,
            booking.booking_id,
            checkout_session_id,
            payment_intent_id=payment_intent_id,
            commit=False,
        )
    elif payment_intent_id and not booking.stripe_payment_intent_id:
        booking.stripe_payment_intent_id = payment_intent_id
        await session.flush()

    await booking_service.record_stripe_deposit_payment(
        session,
        booking,
        amount_cents=int(amount_cents),
        currency=str(currency),
        status=payment_status,
        provider_ref=str(payment_intent_id) if payment_intent_id else None,
        checkout_session_id=str(checkout_session_id)
        if checkout_session_id
        else booking.stripe_checkout_session_id,
        payment_intent_id=str(payment_intent_id) if payment_intent_id else None,
        received_at=received_at,
    )

    if payment_status == invoice_statuses.PAYMENT_STATUS_SUCCEEDED:
        await booking_service.mark_deposit_paid(
            session,
            checkout_session_id=checkout_session_id,
            payment_intent_id=payment_intent_id,
            email_adapter=None,
            commit=False,
        )
    else:
        await booking_service.mark_deposit_failed(
            session,
            checkout_session_id=checkout_session_id,
            payment_intent_id=payment_intent_id,
            failure_status=failure_status or "failed",
            commit=False,
        )

    return True


async def _handle_webhook_event(session: AsyncSession, event: Any, email_adapter: Any) -> bool:
    data = _safe_get(event, "data", {}) or {}
    payload_object = _safe_get(data, "object", {}) or {}
    metadata = _safe_get(payload_object, "metadata", {}) or {}
    if isinstance(metadata, dict) and metadata.get("invoice_id"):
        return await _handle_invoice_event(session, event)
    if isinstance(metadata, dict) and metadata.get("booking_id"):
        return await _handle_deposit_event(session, event, email_adapter)
    logger.info(
        "stripe_webhook_ignored",
        extra={"extra": {"reason": "missing_metadata", "event_type": _safe_get(event, "type")}},
    )
    return False


@router.post(
    "/v1/payments/deposit/checkout",
    status_code=status.HTTP_201_CREATED,
)
async def create_deposit_checkout(
    booking_id: str,
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    booking = await session.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
    if not booking.deposit_required or not booking.deposit_cents:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Deposit not required")
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Stripe not configured")

    stripe_client = stripe_infra.resolve_client(http_request.app.state)
    metadata = {"booking_id": booking.booking_id}
    try:
        checkout_session = stripe_client.create_checkout_session(
            amount_cents=int(booking.deposit_cents),
            currency=settings.deposit_currency,
            success_url=settings.stripe_success_url.replace("{CHECKOUT_SESSION_ID}", "{CHECKOUT_SESSION_ID}"),
            cancel_url=settings.stripe_cancel_url,
            metadata=metadata,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "stripe_checkout_creation_failed",
            extra={"extra": {"booking_id": booking.booking_id, "reason": type(exc).__name__}},
        )
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Stripe checkout unavailable") from exc

    checkout_url = getattr(checkout_session, "url", None) or checkout_session.get("url")
    payment_intent = getattr(checkout_session, "payment_intent", None) or checkout_session.get("payment_intent")
    await booking_service.attach_checkout_session(
        session,
        booking.booking_id,
        getattr(checkout_session, "id", None) or checkout_session.get("id"),
        payment_intent_id=payment_intent,
        commit=False,
    )

    await booking_service.record_stripe_deposit_payment(
        session,
        booking,
        amount_cents=int(booking.deposit_cents),
        currency=settings.deposit_currency,
        status=invoice_statuses.PAYMENT_STATUS_PENDING,
        provider_ref=str(payment_intent) if payment_intent else None,
        checkout_session_id=getattr(checkout_session, "id", None) or checkout_session.get("id"),
        payment_intent_id=str(payment_intent) if payment_intent else None,
        received_at=datetime.now(tz=timezone.utc),
        reference="stripe_checkout",
    )

    await session.commit()
    return {"checkout_url": checkout_url, "provider": "stripe", "booking_id": booking.booking_id}


@router.post(
    "/v1/payments/invoice/checkout",
    response_model=invoice_schemas.InvoicePaymentInitResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_invoice_payment_checkout(
    invoice_id: str,
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> invoice_schemas.InvoicePaymentInitResponse:
    invoice = await session.get(Invoice, invoice_id, options=[selectinload(Invoice.payments)])
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    if invoice.status == invoice_statuses.INVOICE_STATUS_VOID:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invoice is void")
    if invoice.status == invoice_statuses.INVOICE_STATUS_DRAFT:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invoice not sent yet")
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Stripe not configured")

    outstanding = invoice_service.outstanding_balance_cents(invoice)
    if outstanding <= 0:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invoice already paid")

    stripe_client = stripe_infra.resolve_client(http_request.app.state)
    checkout_session = stripe_client.create_checkout_session(
        amount_cents=outstanding,
        currency=invoice.currency.lower(),
        success_url=settings.stripe_invoice_success_url.replace("{INVOICE_ID}", invoice.invoice_id),
        cancel_url=settings.stripe_invoice_cancel_url.replace("{INVOICE_ID}", invoice.invoice_id),
        metadata={"invoice_id": invoice.invoice_id, "invoice_number": invoice.invoice_number},
        payment_intent_metadata={"invoice_id": invoice.invoice_id, "invoice_number": invoice.invoice_number},
        product_name=f"Invoice {invoice.invoice_number}",
    )
    checkout_url = getattr(checkout_session, "url", None) or checkout_session.get("url")
    checkout_id = getattr(checkout_session, "id", None) or checkout_session.get("id")

    await invoice_service.register_payment(
        session,
        invoice,
        provider="stripe",
        provider_ref=None,
        method=invoice_statuses.PAYMENT_METHOD_CARD,
        amount_cents=outstanding,
        currency=invoice.currency,
        status=invoice_statuses.PAYMENT_STATUS_PENDING,
        reference="stripe_checkout",
        checkout_session_id=checkout_id,
    )
    await session.commit()

    logger.info(
        "stripe_invoice_checkout_created",
        extra={
            "extra": {
                "invoice_id": invoice.invoice_id,
                "checkout_session_id": checkout_id,
            }
        },
    )

    return invoice_schemas.InvoicePaymentInitResponse(
        provider="stripe",
        amount_cents=outstanding,
        currency=invoice.currency,
        checkout_url=checkout_url,
        client_secret=None,
    )


async def _stripe_webhook_handler(http_request: Request, session: AsyncSession) -> dict[str, bool]:
    payload = await http_request.body()
    sig_header = http_request.headers.get("Stripe-Signature")
    if not settings.stripe_webhook_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Stripe webhook disabled")

    stripe_client = stripe_infra.resolve_client(http_request.app.state)
    try:
        event = stripe_client.verify_webhook(payload=payload, signature=sig_header)
    except Exception as exc:  # noqa: BLE001
        logger.warning("stripe_webhook_invalid", extra={"extra": {"reason": type(exc).__name__}})
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Stripe webhook") from exc

    event_id = _safe_get(event, "id")
    if not event_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing event id")
    payload_hash = hashlib.sha256(payload or b"").hexdigest()

    processed = False
    processing_error: Exception | None = None
    async with session.begin():
        existing = await session.scalar(select(StripeEvent).where(StripeEvent.event_id == str(event_id)).with_for_update())
        if existing:
            if existing.payload_hash != payload_hash:
                logger.warning(
                    "stripe_webhook_replayed_mismatch",
                    extra={"extra": {"event_id": event_id}},
                )
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Event payload mismatch")

            if existing.status in {"succeeded", "ignored"}:
                logger.info(
                    "stripe_webhook_duplicate",
                    extra={"extra": {"event_id": event_id, "status": existing.status}},
                )
                return {"received": True, "processed": False}

            if existing.status == "processing":
                logger.info(
                    "stripe_webhook_duplicate",
                    extra={"extra": {"event_id": event_id, "status": existing.status}},
                )
                return {"received": True, "processed": False}

            record = existing
            record.status = "processing"
        else:
            record = StripeEvent(event_id=str(event_id), status="processing", payload_hash=payload_hash)
            session.add(record)

        try:
            processed = await _handle_webhook_event(session, event, getattr(http_request.app.state, "email_adapter", None))
            record.status = "succeeded" if processed else "ignored"
        except Exception as exc:  # noqa: BLE001
            processed = False
            record.status = "error"
            processing_error = exc
            logger.exception(
                "stripe_webhook_error",
                extra={"extra": {"event_id": event_id, "reason": type(exc).__name__}},
            )

    if processing_error is not None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stripe webhook processing error",
        ) from processing_error

    return {"received": True, "processed": processed}


@router.post("/v1/payments/stripe/webhook", status_code=status.HTTP_200_OK)
async def stripe_webhook(
    http_request: Request, session: AsyncSession = Depends(get_db_session)
) -> dict[str, bool]:
    return await _stripe_webhook_handler(http_request, session)


@router.post("/stripe/webhook", status_code=status.HTTP_200_OK)
async def legacy_stripe_webhook(
    http_request: Request, session: AsyncSession = Depends(get_db_session)
) -> dict[str, bool]:
    return await _stripe_webhook_handler(http_request, session)

