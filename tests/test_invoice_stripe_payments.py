import asyncio
import hashlib
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import sqlalchemy as sa

from app.domain.invoices import service as invoice_service, statuses as invoice_statuses
from app.domain.invoices.db_models import Invoice, Payment, StripeEvent
from app.infra import stripe_client as stripe_infra
from app.main import app
from app.settings import settings


async def _seed_invoice(
    async_session_maker, total_cents: int = 1000, status: str = invoice_statuses.INVOICE_STATUS_SENT
) -> tuple[str, str]:
    async with async_session_maker() as session:
        invoice = Invoice(
            invoice_number=f"INV-TEST-{uuid.uuid4()}",
            order_id=None,
            customer_id=None,
            status=status,
            issue_date=date.today(),
            currency="CAD",
            subtotal_cents=total_cents,
            tax_cents=0,
            total_cents=total_cents,
        )
        session.add(invoice)
        await session.flush()
        token = await invoice_service.upsert_public_token(session, invoice)
        await session.commit()
        return invoice.invoice_id, token


def test_create_payment_session(client, async_session_maker, monkeypatch):
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test")
    app.state.stripe_client = SimpleNamespace(
        create_checkout_session=lambda **kwargs: SimpleNamespace(id="cs_test_invoice", url="https://stripe.test/checkout"),
        verify_webhook=lambda payload, signature: payload,
    )
    invoice_id, token = asyncio.run(_seed_invoice(async_session_maker, total_cents=2400))

    response = client.post(f"/v1/payments/invoice/checkout?invoice_id={invoice_id}")
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["checkout_url"].startswith("https://stripe.test")
    assert payload["amount_cents"] == 2400
    assert payload["provider"] == "stripe"


def test_create_payment_session_rejects_void_invoice(client, async_session_maker, monkeypatch):
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test")
    invoice_id, _ = asyncio.run(
        _seed_invoice(
            async_session_maker,
            total_cents=2400,
            status=invoice_statuses.INVOICE_STATUS_VOID,
        )
    )

    app.state.stripe_client = SimpleNamespace()

    response = client.post(f"/v1/payments/invoice/checkout?invoice_id={invoice_id}")
    assert response.status_code == 409
    assert response.json()["detail"] == "Invoice is void"


def test_webhook_marks_invoice_paid_and_idempotent(client, async_session_maker, monkeypatch):
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_test")
    invoice_id, _ = asyncio.run(_seed_invoice(async_session_maker, total_cents=5000))

    event = {
        "id": "evt_test_invoice",
        "type": "payment_intent.succeeded",
        "created": int(datetime.now(tz=timezone.utc).timestamp()),
        "data": {
            "object": {
                "id": "pi_test_invoice",
                "amount_received": 5000,
                "currency": "CAD",
                "metadata": {"invoice_id": invoice_id},
            }
        },
    }

    app.state.stripe_client = SimpleNamespace(verify_webhook=lambda payload, signature: event)

    headers = {"Stripe-Signature": "t=test"}
    first = client.post("/v1/payments/stripe/webhook", content=b"{}", headers=headers)
    assert first.status_code == 200, first.text
    second = client.post("/stripe/webhook", content=b"{}", headers=headers)
    assert second.status_code == 200
    assert second.json()["processed"] is False

    async def _fetch_invoice_status() -> tuple[str, int]:
        async with async_session_maker() as session:
            invoice = await session.get(Invoice, invoice_id)
            paid = await session.scalar(
                sa.select(sa.func.count()).select_from(Payment).where(Payment.invoice_id == invoice_id)
            )
            assert invoice is not None
            return invoice.status, int(paid or 0)

    status_value, payment_count = asyncio.run(_fetch_invoice_status())
    assert status_value == invoice_statuses.INVOICE_STATUS_PAID
    assert payment_count == 1


def test_webhook_signature_verification(client, monkeypatch):
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_test")

    app.state.stripe_client = SimpleNamespace(verify_webhook=lambda **_: (_ for _ in ()).throw(ValueError("invalid signature")))

    response = client.post("/v1/payments/stripe/webhook", content=b"{}", headers={"Stripe-Signature": "invalid"})
    assert response.status_code == 400


def test_webhook_retries_after_processing_error(client, async_session_maker, monkeypatch):
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_test")
    invoice_id, _ = asyncio.run(_seed_invoice(async_session_maker, total_cents=4200))
    payload = b"{}"

    event = {
        "id": "evt_retry_once",
        "type": "payment_intent.succeeded",
        "created": int(datetime.now(tz=timezone.utc).timestamp()),
        "data": {
            "object": {
                "id": "pi_retry_once",
                "amount_received": 4200,
                "currency": "CAD",
                "metadata": {"invoice_id": invoice_id},
            }
        },
    }

    call_count = {"value": 0}
    real_record_payment = invoice_service.record_stripe_payment

    async def _record_payment(session, invoice, **kwargs):
        call_count["value"] += 1
        if call_count["value"] == 1:
            raise RuntimeError("transient failure")
        return await real_record_payment(session=session, invoice=invoice, **kwargs)

    app.state.stripe_client = SimpleNamespace(verify_webhook=lambda **_: event)
    monkeypatch.setattr(invoice_service, "record_stripe_payment", _record_payment)

    headers = {"Stripe-Signature": "t=test"}
    first = client.post("/v1/payments/stripe/webhook", content=payload, headers=headers)
    assert first.status_code == 500

    async def _fetch_state() -> tuple[str | None, str | None, int]:
        async with async_session_maker() as session:
            invoice = await session.get(Invoice, invoice_id)
            event_record = await session.get(StripeEvent, "evt_retry_once")
            payment_count = await session.scalar(
                sa.select(sa.func.count()).select_from(Payment).where(Payment.invoice_id == invoice_id)
            )
            return (
                event_record.status if event_record else None,
                invoice.status if invoice else None,
                int(payment_count or 0),
            )

    status_value, invoice_status, payment_count = asyncio.run(_fetch_state())
    assert status_value == "error"
    assert invoice_status == invoice_statuses.INVOICE_STATUS_SENT
    assert payment_count == 0

    second = client.post("/stripe/webhook", content=payload, headers=headers)
    assert second.status_code == 200
    assert second.json()["processed"] is True

    status_value, invoice_status, payment_count = asyncio.run(_fetch_state())
    assert status_value == "succeeded"
    assert invoice_status == invoice_statuses.INVOICE_STATUS_PAID
    assert payment_count == 1


def test_webhook_retries_after_error(client, async_session_maker, monkeypatch):
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_test")
    invoice_id, _ = asyncio.run(_seed_invoice(async_session_maker, total_cents=4200))
    payload = b"{}"
    payload_hash = hashlib.sha256(payload).hexdigest()

    async def _seed_event() -> None:
        async with async_session_maker() as session:
            session.add(StripeEvent(event_id="evt_retry", status="error", payload_hash=payload_hash))
            await session.commit()

    asyncio.run(_seed_event())

    event = {
        "id": "evt_retry",
        "type": "payment_intent.succeeded",
        "created": int(datetime.now(tz=timezone.utc).timestamp()),
        "data": {
            "object": {
                "id": "pi_retry",
                "amount_received": 4200,
                "currency": "CAD",
                "metadata": {"invoice_id": invoice_id},
            }
        },
    }

    app.state.stripe_client = SimpleNamespace(verify_webhook=lambda **_: event)

    headers = {"Stripe-Signature": "t=test"}
    response = client.post("/v1/payments/stripe/webhook", content=payload, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["processed"] is True


def test_checkout_then_payment_intent_single_payment(client, async_session_maker, monkeypatch):
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_test")
    invoice_id, _ = asyncio.run(_seed_invoice(async_session_maker, total_cents=5500))

    checkout_event = {
        "id": "evt_invoice_checkout",
        "type": "checkout.session.completed",
        "created": int(datetime.now(tz=timezone.utc).timestamp()),
        "data": {
            "object": {
                "id": "cs_invoice_multi",
                "payment_intent": "pi_invoice_multi",
                "payment_status": "paid",
                "amount_total": 5500,
                "currency": "CAD",
                "metadata": {"invoice_id": invoice_id},
            }
        },
    }
    payment_intent_event = {
        "id": "evt_invoice_payment_intent",
        "type": "payment_intent.succeeded",
        "created": int(datetime.now(tz=timezone.utc).timestamp()),
        "data": {
            "object": {
                "id": "pi_invoice_multi",
                "amount_received": 5500,
                "currency": "CAD",
                "metadata": {"invoice_id": invoice_id},
            }
        },
    }

    events = iter([checkout_event, payment_intent_event])

    def _verify_webhook(payload, signature):
        return next(events)

    app.state.stripe_client = SimpleNamespace(verify_webhook=_verify_webhook)

    headers = {"Stripe-Signature": "t=test"}
    first = client.post("/v1/payments/stripe/webhook", content=b"{}", headers=headers)
    assert first.status_code == 200, first.text
    second = client.post("/v1/payments/stripe/webhook", content=b"{}", headers=headers)
    assert second.status_code == 200, second.text

    async def _fetch_state() -> tuple[int, str | None, str | None]:
        async with async_session_maker() as session:
            payment_count = await session.scalar(
                sa.select(sa.func.count()).select_from(Payment).where(Payment.invoice_id == invoice_id)
            )
            provider_ref = await session.scalar(
                sa.select(Payment.provider_ref)
                .where(Payment.invoice_id == invoice_id)
                .limit(1)
            )
            invoice = await session.get(Invoice, invoice_id)
            return int(payment_count or 0), provider_ref, invoice.status if invoice else None

    payment_count, provider_ref, invoice_status = asyncio.run(_fetch_state())
    assert payment_count == 1
    assert provider_ref == "pi_invoice_multi"
    assert invoice_status == invoice_statuses.INVOICE_STATUS_PAID

