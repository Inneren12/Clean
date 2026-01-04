from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api import entitlements
from app.domain.bookings import service as booking_service
from app.domain.bookings.db_models import Booking
from app.domain.invoices import schemas as invoice_schemas, service as invoice_service, statuses as invoice_statuses
from app.domain.invoices.db_models import Invoice, StripeEvent
from app.domain.saas import billing_service
from app.domain.saas.plans import get_plan
from app.infra.email import resolve_app_email_adapter
from app.infra import stripe_client as stripe_infra
from app.infra.db import get_db_session
from app.infra.metrics import metrics
from app.shared.circuit_breaker import CircuitBreakerOpenError
from app.settings import settings

router = APIRouter()
logger = logging.getLogger(__name__)


def _stripe_client(request: Request):
    if getattr(request.app.state, "stripe_client", None):
        return request.app.state.stripe_client
    services = getattr(request.app.state, "services", None)
    if services and getattr(services, "stripe_client", None):
        return services.stripe_client
    return stripe_infra.resolve_client(request.app.state)


class StripeOrgResolutionError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass
class StripeOrgContext:
    org_id: uuid.UUID | None
    invoice: Invoice | None = None
    booking: Booking | None = None


def _safe_get(source: object, key: str, default: Any | None = None) -> Any:
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _extract_event_metadata_ids(event: Any) -> tuple[str | None, str | None]:
    data = _safe_get(event, "data", {}) or {}
    payload_object = _safe_get(data, "object", {}) or {}
    metadata = _safe_get(payload_object, "metadata", {}) or {}
    invoice_id = metadata.get("invoice_id") if isinstance(metadata, dict) else None
    booking_id = metadata.get("booking_id") if isinstance(metadata, dict) else None
    return invoice_id, booking_id


def _coerce_event_created_at(event: Any) -> datetime | None:
    created_raw = _safe_get(event, "created")
    if isinstance(created_raw, (int, float)):
        return datetime.fromtimestamp(created_raw, tz=timezone.utc)
    if isinstance(created_raw, datetime):
        return created_raw.astimezone(timezone.utc)
    return None


async def _lock_invoice(session: AsyncSession, invoice_id: str) -> Invoice | None:
    stmt = select(Invoice).where(Invoice.invoice_id == invoice_id).with_for_update()
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def _lock_booking(session: AsyncSession, booking_id: str) -> Booking | None:
    stmt = select(Booking).where(Booking.booking_id == booking_id).with_for_update()
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def _lock_booking_by_checkout_or_intent(
    session: AsyncSession, checkout_session_id: str | None, payment_intent_id: str | None
) -> Booking | None:
    conditions = []
    if checkout_session_id:
        conditions.append(Booking.stripe_checkout_session_id == checkout_session_id)
    if payment_intent_id:
        conditions.append(Booking.stripe_payment_intent_id == payment_intent_id)

    if not conditions:
        return None

    stmt = select(Booking).where(or_(*conditions)).with_for_update()
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def _resolve_org_for_event(session: AsyncSession, event: Any) -> StripeOrgContext:
    data = _safe_get(event, "data", {}) or {}
    payload_object = _safe_get(data, "object", {}) or {}
    metadata = _safe_get(payload_object, "metadata", {}) or {}
    invoice_id = metadata.get("invoice_id") if isinstance(metadata, dict) else None
    booking_id = metadata.get("booking_id") if isinstance(metadata, dict) else None
    customer_id = _safe_get(payload_object, "customer")
    org_id_raw = metadata.get("org_id") if isinstance(metadata, dict) else None

    if invoice_id:
        invoice = await _lock_invoice(session, invoice_id)
        if invoice is None:
            raise StripeOrgResolutionError("invoice_not_found")
        return StripeOrgContext(org_id=invoice.org_id, invoice=invoice)

    if booking_id:
        booking = await _lock_booking(session, booking_id)
        if booking is None:
            raise StripeOrgResolutionError("booking_not_found")
        return StripeOrgContext(org_id=booking.org_id, booking=booking)

    event_type = _safe_get(event, "type", "") or ""
    checkout_id_raw = _safe_get(payload_object, "id")
    checkout_session_id = checkout_id_raw if isinstance(checkout_id_raw, str) and checkout_id_raw.startswith("cs_") else None
    payment_intent_id = (
        _safe_get(payload_object, "payment_intent")
        if event_type.startswith("checkout.session.")
        else _safe_get(payload_object, "id") if event_type.startswith("payment_intent.") else None
    )

    booking_from_payment = await _lock_booking_by_checkout_or_intent(
        session, checkout_session_id, payment_intent_id
    )
    if booking_from_payment:
        return StripeOrgContext(org_id=booking_from_payment.org_id, booking=booking_from_payment)

    org_id: uuid.UUID | None = None
    if org_id_raw:
        try:
            org_id = uuid.UUID(str(org_id_raw))
        except Exception as exc:  # noqa: BLE001
            raise StripeOrgResolutionError("invalid_org") from exc

    billing = None
    if customer_id:
        billing = await billing_service.get_billing_by_customer(session, str(customer_id))

    if billing and org_id and billing.org_id != org_id:
        raise StripeOrgResolutionError("org_customer_mismatch")

    if billing:
        return StripeOrgContext(org_id=billing.org_id)

    if org_id:
        # Verify org exists
        from app.domain.saas.db_models import Organization
        org_exists = await session.scalar(select(Organization.org_id).where(Organization.org_id == org_id))
        if not org_exists:
            raise StripeOrgResolutionError("org_not_found")
        return StripeOrgContext(org_id=org_id)

    raise StripeOrgResolutionError("missing_org")


async def _handle_invoice_event(session: AsyncSession, event: Any, ctx: StripeOrgContext) -> bool:
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

    invoice = ctx.invoice or await _lock_invoice(session, invoice_id)
    if invoice is None:
        logger.info(
            "stripe_invoice_event_ignored",
            extra={"extra": {"reason": "invoice_not_found", "invoice_id": invoice_id}},
        )
        return False

    if ctx.org_id and invoice.org_id != ctx.org_id:
        logger.info(
            "stripe_invoice_event_ignored",
            extra={"extra": {"reason": "org_mismatch", "invoice_id": invoice.invoice_id}},
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

    if payment_status == invoice_statuses.PAYMENT_STATUS_FAILED:
        await invoice_service.enqueue_dunning_email(
            session, invoice, failure_reason=str(event_type)
        )

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


async def _handle_deposit_event(
    session: AsyncSession, event: Any, email_adapter: Any, ctx: StripeOrgContext
) -> bool:
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

    booking = ctx.booking or await _lock_booking(session, booking_id)
    if booking is None:
        logger.info(
            "stripe_deposit_event_ignored",
            extra={"extra": {"reason": "booking_not_found", "booking_id": booking_id}},
        )
        return False

    if ctx.org_id and booking.org_id != ctx.org_id:
        logger.info(
            "stripe_deposit_event_ignored",
            extra={"extra": {"reason": "org_mismatch", "booking_id": booking.booking_id}},
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


async def _handle_subscription_event(session: AsyncSession, event: Any, ctx: StripeOrgContext) -> bool:
    event_type = _safe_get(event, "type", "") or ""
    data = _safe_get(event, "data", {}) or {}
    payload_object = _safe_get(data, "object", {}) or {}
    metadata = _safe_get(payload_object, "metadata", {}) or {}

    if str(event_type).startswith("customer.subscription"):
        billing = await billing_service.update_from_subscription_payload(session, event)
        if billing is not None and ctx.org_id and billing.org_id != ctx.org_id:
            logger.warning(
                "stripe_subscription_org_mismatch",
                extra={"extra": {"event_type": event_type, "billing_org": str(billing.org_id), "ctx_org": str(ctx.org_id)}},
            )
            return False
        return billing is not None

    mode = _safe_get(payload_object, "mode")
    if mode != "subscription":
        return False

    plan_id = metadata.get("plan_id") if isinstance(metadata, dict) else None
    subscription_id = _safe_get(payload_object, "subscription") or _safe_get(payload_object, "id")
    customer_id = _safe_get(payload_object, "customer")
    period_end_ts = _safe_get(payload_object, "current_period_end")
    status = _safe_get(payload_object, "status")

    org_id = ctx.org_id
    if not org_id:
        logger.info(
            "stripe_subscription_event_ignored",
            extra={"extra": {"reason": "missing_org", "event_type": event_type}},
        )
        return False

    current_period_end = None
    if period_end_ts:
        current_period_end = datetime.fromtimestamp(int(period_end_ts), tz=timezone.utc)

    await billing_service.set_plan(
        session,
        org_id,
        plan_id=get_plan(plan_id).plan_id,
        status=status,
        stripe_customer_id=customer_id if customer_id else None,
        stripe_subscription_id=str(subscription_id) if subscription_id else None,
        current_period_end=current_period_end,
    )
    return True


async def _handle_webhook_event(
    session: AsyncSession, event: Any, email_adapter: Any, ctx: StripeOrgContext
) -> bool:
    event_type = _safe_get(event, "type")
    data = _safe_get(event, "data", {}) or {}
    payload_object = _safe_get(data, "object", {}) or {}
    metadata = _safe_get(payload_object, "metadata", {}) or {}
    if isinstance(metadata, dict) and metadata.get("invoice_id"):
        return await _handle_invoice_event(session, event, ctx)
    if isinstance(metadata, dict) and metadata.get("booking_id"):
        return await _handle_deposit_event(session, event, email_adapter, ctx)
    event_type = _safe_get(event, "type")
    session_id = _safe_get(payload_object, "id")
    payment_intent_id = _safe_get(payload_object, "payment_intent") or _safe_get(payload_object, "id")
    payment_status = _safe_get(payload_object, "payment_status")
    if event_type == "checkout.session.completed" and payment_status == "paid":
        await booking_service.mark_deposit_paid(
            session=session,
            checkout_session_id=session_id,
            payment_intent_id=payment_intent_id,
            email_adapter=email_adapter,
            commit=False,
        )
        return True
    if event_type in {"checkout.session.expired", "payment_intent.payment_failed"}:
        await booking_service.mark_deposit_failed(
            session=session,
            checkout_session_id=session_id,
            payment_intent_id=payment_intent_id,
            failure_status="expired" if event_type == "checkout.session.expired" else "failed",
            commit=False,
        )
        return True
    if (
        str(event_type or "").startswith("customer.subscription")
        or _safe_get(payload_object, "mode") == "subscription"
        or _safe_get(payload_object, "object") == "subscription"
    ):
        return await _handle_subscription_event(session, event, ctx)
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
    org_id = entitlements.resolve_org_id(http_request)
    booking = await session.get(Booking, booking_id)
    if booking is None or booking.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
    if not booking.deposit_required or not booking.deposit_cents:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Deposit not required")
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Stripe not configured")

    stripe_client = _stripe_client(http_request)
    metadata = {"booking_id": booking.booking_id}
    try:
        checkout_session = await stripe_infra.call_stripe_client_method(
            stripe_client,
            "create_checkout_session",
            amount_cents=int(booking.deposit_cents),
            currency=settings.deposit_currency,
            success_url=settings.stripe_success_url.replace("{CHECKOUT_SESSION_ID}", "{CHECKOUT_SESSION_ID}"),
            cancel_url=settings.stripe_cancel_url,
            metadata=metadata,
        )
    except CircuitBreakerOpenError as exc:
        logger.warning(
            "stripe_checkout_circuit_open",
            extra={"extra": {"booking_id": booking.booking_id, "reason": type(exc).__name__}},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe temporarily unavailable",
        ) from exc
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
    org_id = entitlements.resolve_org_id(http_request)
    invoice = await session.get(Invoice, invoice_id, options=[selectinload(Invoice.payments)])
    if invoice is None or invoice.org_id != org_id:
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

    stripe_client = _stripe_client(http_request)
    try:
        checkout_session = await stripe_infra.call_stripe_client_method(
            stripe_client,
            "create_checkout_session",
            amount_cents=outstanding,
            currency=invoice.currency.lower(),
            success_url=settings.stripe_invoice_success_url.replace("{INVOICE_ID}", invoice.invoice_id),
            cancel_url=settings.stripe_invoice_cancel_url.replace("{INVOICE_ID}", invoice.invoice_id),
            metadata={"invoice_id": invoice.invoice_id, "invoice_number": invoice.invoice_number},
            payment_intent_metadata={"invoice_id": invoice.invoice_id, "invoice_number": invoice.invoice_number},
            product_name=f"Invoice {invoice.invoice_number}",
        )
    except CircuitBreakerOpenError as exc:
        logger.warning(
            "stripe_invoice_checkout_circuit_open",
            extra={"extra": {"invoice_id": invoice.invoice_id, "reason": type(exc).__name__}},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe temporarily unavailable",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "stripe_invoice_checkout_failed",
            extra={"extra": {"invoice_id": invoice.invoice_id, "reason": type(exc).__name__}},
        )
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Stripe checkout unavailable") from exc
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

    outcome = "error"
    try:
        stripe_client = _stripe_client(http_request)
        try:
            event = await stripe_infra.call_stripe_client_method(
                stripe_client, "verify_webhook", payload=payload, signature=sig_header
            )
        except CircuitBreakerOpenError as exc:
            metrics.record_webhook_error("stripe_unavailable")
            metrics.record_stripe_circuit_open()
            logger.warning("stripe_webhook_circuit_open", extra={"extra": {"reason": type(exc).__name__}})
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Stripe temporarily unavailable",
            ) from exc
        except Exception as exc:  # noqa: BLE001
            metrics.record_webhook("error")
            metrics.record_webhook_error("invalid_signature")
            logger.warning("stripe_webhook_invalid", extra={"extra": {"reason": type(exc).__name__}})
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Stripe webhook") from exc

        event_id = _safe_get(event, "id")
        if not event_id:
            metrics.record_webhook_error("missing_event_id")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing event id")
        payload_hash = hashlib.sha256(payload or b"").hexdigest()
        event_type = _safe_get(event, "type")
        event_created_at = _coerce_event_created_at(event)
        metadata_invoice_id, metadata_booking_id = _extract_event_metadata_ids(event)

        processed = False
        processing_error: Exception | None = None
        async with session.begin():
            try:
                ctx = await _resolve_org_for_event(session, event)
            except StripeOrgResolutionError as exc:
                # Differentiate between security violations (400) and non-actionable events (200)
                # Security violations: org_customer_mismatch (consistency violation)
                # Non-actionable: invoice_not_found, booking_not_found, missing_org, invalid_org, org_not_found
                if exc.reason == "org_customer_mismatch":
                    logger.warning(
                        "stripe_webhook_org_mismatch",
                        extra={"extra": {"event_id": event_id, "reason": exc.reason}},
                    )
                    metrics.record_webhook_error("org_resolution_conflict")
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Org conflict") from exc

                # Non-actionable: log, record metric, return 200
                logger.info(
                    "stripe_webhook_ignored_unresolvable",
                    extra={"extra": {"event_id": event_id, "reason": exc.reason}},
                )
                metrics.record_webhook("ignored")
                metrics.record_webhook_error(f"org_resolution_{exc.reason}")
                outcome = "ignored"
                return {"received": True, "processed": False}

            existing = await session.scalar(
                select(StripeEvent).where(StripeEvent.event_id == str(event_id)).with_for_update()
            )
            if existing:
                if existing.org_id and ctx.org_id and existing.org_id != ctx.org_id:
                    logger.warning(
                        "stripe_webhook_org_conflict",
                        extra={
                            "extra": {
                                "event_id": event_id,
                                "expected_org": str(existing.org_id),
                                "resolved_org": str(ctx.org_id),
                            }
                        },
                    )
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Stripe event org mismatch")
                if existing.payload_hash != payload_hash:
                    logger.warning(
                        "stripe_webhook_replayed_mismatch",
                        extra={"extra": {"event_id": event_id}},
                    )
                    metrics.record_webhook_error("payload_mismatch")
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Event payload mismatch")

                if existing.status in {"succeeded", "ignored"}:
                    logger.info(
                        "stripe_webhook_duplicate",
                        extra={"extra": {"event_id": event_id, "status": existing.status}},
                    )
                    metrics.record_webhook("ignored")
                    outcome = "ignored"
                    return {"received": True, "processed": False}

                if existing.status == "processing":
                    logger.info(
                        "stripe_webhook_duplicate",
                        extra={"extra": {"event_id": event_id, "status": existing.status}},
                    )
                    metrics.record_webhook("ignored")
                    outcome = "ignored"
                    return {"received": True, "processed": False}

                record = existing
                record.status = "processing"
                if record.org_id is None and ctx.org_id:
                    record.org_id = ctx.org_id
                if not record.event_type:
                    record.event_type = event_type
                if record.event_created_at is None:
                    record.event_created_at = event_created_at
                if not record.invoice_id:
                    record.invoice_id = metadata_invoice_id or getattr(ctx.invoice, "invoice_id", None)
                if not record.booking_id:
                    record.booking_id = metadata_booking_id or getattr(ctx.booking, "booking_id", None)
            else:
                invoice_id = getattr(ctx.invoice, "invoice_id", None) or metadata_invoice_id
                booking_id = getattr(ctx.booking, "booking_id", None) or metadata_booking_id
                record = StripeEvent(
                    event_id=str(event_id),
                    status="processing",
                    payload_hash=payload_hash,
                    org_id=ctx.org_id,
                    event_type=event_type,
                    event_created_at=event_created_at,
                    invoice_id=invoice_id,
                    booking_id=booking_id,
                )
                session.add(record)

            try:
                processed = await _handle_webhook_event(
                    session, event, resolve_app_email_adapter(http_request), ctx
                )
                record.status = "succeeded" if processed else "ignored"
            except Exception as exc:  # noqa: BLE001
                processed = False
                record.status = "error"
                processing_error = exc
                record.last_error = str(exc)
                logger.exception(
                    "stripe_webhook_error",
                    extra={"extra": {"event_id": event_id, "reason": type(exc).__name__}},
                )
                metrics.record_webhook("error")
                metrics.record_webhook_error("processing_error")
            else:
                record.last_error = None

        if processing_error is not None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Stripe webhook processing error",
            ) from processing_error

        metrics.record_webhook("processed" if processed else "ignored")
        outcome = "processed" if processed else "ignored"
        return {"received": True, "processed": processed}
    finally:
        metrics.record_stripe_webhook(outcome)


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

