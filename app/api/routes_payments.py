from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.invoices import service as invoice_service, statuses as invoice_statuses
from app.domain.invoices.db_models import Invoice, StripeEvent
from app.infra import stripe as stripe_infra
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


async def _handle_payment_event(session: AsyncSession, event: Any) -> bool:
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
    if event_type == "checkout.session.completed":
        payment_status = invoice_statuses.PAYMENT_STATUS_SUCCEEDED if _safe_get(payload_object, "payment_status") == "paid" else None
    elif event_type == "payment_intent.succeeded":
        payment_status = invoice_statuses.PAYMENT_STATUS_SUCCEEDED
    elif event_type == "payment_intent.payment_failed":
        payment_status = invoice_statuses.PAYMENT_STATUS_FAILED

    if payment_status is None:
        logger.info(
            "stripe_invoice_event_ignored",
            extra={"extra": {"reason": "unsupported_event", "event_type": event_type}},
        )
        return False

    provider_ref = _safe_get(payload_object, "payment_intent") or _safe_get(payload_object, "id")
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


@router.post("/stripe/webhook", status_code=status.HTTP_200_OK)
async def stripe_invoice_webhook(
    http_request: Request, session: AsyncSession = Depends(get_db_session)
) -> dict[str, bool]:
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

    event_id = _safe_get(event, "id")
    if not event_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing event id")
    payload_hash = hashlib.sha256(payload or b"").hexdigest()

    processed = False
    processing_error: Exception | None = None

    async with session.begin():
        existing = await session.scalar(
            select(StripeEvent).where(StripeEvent.event_id == str(event_id)).with_for_update()
        )
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
            processed = await _handle_payment_event(session, event)
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
