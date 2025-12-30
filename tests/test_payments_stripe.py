import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import func, select

from app.domain.bookings.db_models import Booking
from app.domain.invoices import statuses as invoice_statuses
from app.domain.invoices.db_models import Payment, StripeEvent
from app.main import app
from app.settings import settings


async def _seed_booking(async_session_maker) -> str:
    async with async_session_maker() as session:
        booking = Booking(
            team_id=1,
            starts_at=datetime.now(tz=timezone.utc),
            duration_minutes=60,
            status="PENDING",
            deposit_required=True,
            deposit_cents=5000,
            deposit_policy=["test"],
            deposit_status="pending",
        )
        session.add(booking)
        await session.commit()
        return booking.booking_id


def test_deposit_checkout_and_webhook(client, async_session_maker, monkeypatch):
    settings.stripe_secret_key = "sk_test"
    settings.stripe_webhook_secret = "whsec_test"
    booking_id = asyncio.run(_seed_booking(async_session_maker))

    app.state.stripe_client = SimpleNamespace(
        create_checkout_session=lambda **kwargs: SimpleNamespace(
            id="cs_dep", url="https://stripe.test/deposit", payment_intent="pi_dep"
        ),
        verify_webhook=lambda payload, signature: {
            "id": "evt_dep",
            "type": "checkout.session.completed",
            "created": int(datetime.now(tz=timezone.utc).timestamp()),
            "data": {
                "object": {
                    "id": "cs_dep",
                    "payment_intent": "pi_dep",
                    "payment_status": "paid",
                    "amount_total": 5000,
                    "currency": "CAD",
                    "metadata": {"booking_id": booking_id},
                }
            },
        },
    )

    checkout = client.post(f"/v1/payments/deposit/checkout?booking_id={booking_id}")
    assert checkout.status_code == 201, checkout.text
    body = checkout.json()
    assert body["checkout_url"].startswith("https://stripe.test/deposit")

    webhook = client.post("/v1/payments/stripe/webhook", content=b"{}", headers={"Stripe-Signature": "t=test"})
    assert webhook.status_code == 200
    assert webhook.json()["processed"] is True

    async def _fetch() -> tuple[str | None, int, int]:
        async with async_session_maker() as session:
            booking = await session.get(Booking, booking_id)
            payments = await session.scalar(select(func.count()).select_from(Payment).where(Payment.booking_id == booking_id))
            events = await session.get(StripeEvent, "evt_dep")
            return booking.deposit_status if booking else None, int(payments or 0), 0 if events is None else 1

    deposit_status, payment_count, event_count = asyncio.run(_fetch())
    assert deposit_status == "paid"
    assert payment_count == 1
    assert event_count == 1

    duplicate = client.post("/v1/payments/stripe/webhook", content=b"{}", headers={"Stripe-Signature": "t=test"})
    assert duplicate.status_code == 200
    assert duplicate.json()["processed"] is False


def test_invoice_checkout_stores_pending_payment(client, async_session_maker, monkeypatch):
    settings.stripe_secret_key = "sk_test"
    invoice_id, _ = asyncio.run(
        _seed_invoice(async_session_maker, total_cents=3200, status=invoice_statuses.INVOICE_STATUS_SENT)
    )

    app.state.stripe_client = SimpleNamespace(
        create_checkout_session=lambda **kwargs: SimpleNamespace(id="cs_inv", url="https://stripe.test/invoice"),
        verify_webhook=lambda payload, signature: payload,
    )

    response = client.post(f"/v1/payments/invoice/checkout?invoice_id={invoice_id}")
    assert response.status_code == 201

    async def _fetch_payment() -> tuple[int, str | None]:
        async with async_session_maker() as session:
            record = await session.scalar(select(Payment).where(Payment.checkout_session_id == "cs_inv"))
            return (record.amount_cents if record else 0, record.status if record else None)

    amount, status = asyncio.run(_fetch_payment())
    assert amount == 3200
    assert status == invoice_statuses.PAYMENT_STATUS_PENDING


async def _seed_invoice(async_session_maker, total_cents: int, status: str):
    from app.domain.invoices.db_models import Invoice

    async with async_session_maker() as session:
        invoice = Invoice(
            invoice_number=f"INV-NEW-{datetime.now(tz=timezone.utc).timestamp()}",
            order_id=None,
            customer_id=None,
            status=status,
            issue_date=datetime.now(tz=timezone.utc).date(),
            currency="CAD",
            subtotal_cents=total_cents,
            tax_cents=0,
            total_cents=total_cents,
        )
        session.add(invoice)
        await session.commit()
        return invoice.invoice_id, ""

