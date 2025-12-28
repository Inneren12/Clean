import asyncio
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import sqlalchemy as sa

from app.domain.invoices import service as invoice_service, statuses as invoice_statuses
from app.domain.invoices.db_models import Invoice, Payment
from app.infra import stripe as stripe_infra
from app.main import app
from app.settings import settings


async def _seed_invoice(async_session_maker, total_cents: int = 1000) -> tuple[str, str]:
    async with async_session_maker() as session:
        invoice = Invoice(
            invoice_number=f"INV-TEST-{uuid.uuid4()}",
            order_id=None,
            customer_id=None,
            status=invoice_statuses.INVOICE_STATUS_SENT,
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
    invoice_id, token = asyncio.run(_seed_invoice(async_session_maker, total_cents=2400))

    def _fake_checkout_session(**kwargs):
        assert kwargs["metadata"]["invoice_id"] == invoice_id
        return SimpleNamespace(id="cs_test_invoice", url="https://stripe.test/checkout")

    monkeypatch.setattr(stripe_infra, "create_checkout_session", _fake_checkout_session)

    response = client.post(f"/i/{token}/pay")
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["checkout_url"].startswith("https://stripe.test")
    assert payload["amount_cents"] == 2400
    assert payload["provider"] == "stripe"


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

    def _parse_webhook_event(**_: object):
        return event

    monkeypatch.setattr(stripe_infra, "parse_webhook_event", _parse_webhook_event)
    app.state.stripe_client = SimpleNamespace()

    headers = {"Stripe-Signature": "t=test"}
    first = client.post("/stripe/webhook", data=b"{}", headers=headers)
    assert first.status_code == 200, first.text
    second = client.post("/stripe/webhook", data=b"{}", headers=headers)
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

    def _parse_webhook_event(**_: object):
        raise ValueError("invalid signature")

    monkeypatch.setattr(stripe_infra, "parse_webhook_event", _parse_webhook_event)
    app.state.stripe_client = SimpleNamespace()

    response = client.post("/stripe/webhook", data=b"{}", headers={"Stripe-Signature": "invalid"})
    assert response.status_code == 400
