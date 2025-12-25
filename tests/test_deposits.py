import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import stripe
import sqlalchemy as sa

from app.domain.bookings.db_models import Booking
from app.domain.leads.db_models import Lead
from app.main import app
from app.settings import settings


class StubCheckoutSession:
    def __init__(self, session_id: str, url: str, payment_intent: str):
        self.id = session_id
        self.url = url
        self.payment_intent = payment_intent


def _stub_stripe(session_id: str) -> object:
    def _create(**_: object) -> StubCheckoutSession:
        return StubCheckoutSession(session_id, "https://example.com/checkout", "pi_test")

    checkout = SimpleNamespace(Session=SimpleNamespace(create=staticmethod(_create)))
    return SimpleNamespace(api_key=None, checkout=checkout, Webhook=stripe.Webhook)


def _seed_lead(async_session_maker) -> str:
    async def _create() -> str:
        async with async_session_maker() as session:
            lead = Lead(
                name="Deposit Lead",
                phone="780-555-9999",
                email="deposit@example.com",
                postal_code="T5A",
                preferred_dates=["Sat"],
                structured_inputs={"beds": 2, "baths": 2, "cleaning_type": "deep"},
                estimate_snapshot={
                    "pricing_config_version": "v1",
                    "config_hash": "hash",
                    "total_before_tax": 200.0,
                },
                pricing_config_version="v1",
                config_hash="hash",
                status="NEW",
            )
            session.add(lead)
            await session.commit()
            await session.refresh(lead)
            return lead.lead_id

    import asyncio

    return asyncio.run(_create())


def _booking_start_on_weekend() -> str:
    saturday = datetime.now(tz=timezone.utc) + timedelta(days=(5 - datetime.now(tz=timezone.utc).weekday()) % 7)
    start = saturday.replace(hour=10, minute=0, second=0, microsecond=0)
    return start.isoformat()


def test_booking_response_includes_deposit_policy(client, async_session_maker, monkeypatch):
    settings.stripe_secret_key = "sk_test"
    settings.stripe_webhook_secret = "whsec_test"
    original_client = getattr(app.state, "stripe_client", None)
    try:
        app.state.stripe_client = _stub_stripe("cs_test_deposit")
        lead_id = _seed_lead(async_session_maker)

        payload = {
            "starts_at": _booking_start_on_weekend(),
            "time_on_site_hours": 2,
            "lead_id": lead_id,
        }
        response = client.post("/v1/bookings", json=payload)
        assert response.status_code == 201, response.text
        data = response.json()
        assert data["deposit_required"] is True
        assert data["deposit_cents"] == 5000
        assert set(data["deposit_policy"]) >= {"heavy_cleaning", "weekend", "new_client"}
        assert data["checkout_url"] == "https://example.com/checkout"
    finally:
        app.state.stripe_client = original_client


def test_webhook_confirms_booking(client, async_session_maker):
    settings.stripe_secret_key = "sk_test"
    settings.stripe_webhook_secret = "whsec_test"
    original_client = getattr(app.state, "stripe_client", None)
    try:
        app.state.stripe_client = _stub_stripe("cs_webhook")
        lead_id = _seed_lead(async_session_maker)

        payload = {
            "starts_at": _booking_start_on_weekend(),
            "time_on_site_hours": 2,
            "lead_id": lead_id,
        }
        creation = client.post("/v1/bookings", json=payload)
        assert creation.status_code == 201

        event = {
            "id": "evt_test",
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_webhook", "payment_intent": "pi_live", "payment_status": "paid"}},
        }
        body = json.dumps(event)
        timestamp = int(time.time())
        signed_payload = f"{timestamp}.{body}"
        signature = stripe.WebhookSignature._compute_signature(signed_payload, settings.stripe_webhook_secret)
        headers = {"Stripe-Signature": f"t={timestamp},v1={signature}"}

        webhook_response = client.post("/v1/stripe/webhook", data=body, headers=headers)
        assert webhook_response.status_code == 200

        async def _fetch() -> Booking:
            async with async_session_maker() as session:
                result = await session.execute(sa.select(Booking).limit(1))
                return result.scalar_one()

        import asyncio

        booking = asyncio.run(_fetch())
        assert booking.status == "CONFIRMED"
        assert booking.deposit_status == "paid"
        assert booking.stripe_payment_intent_id == "pi_live"
    finally:
        app.state.stripe_client = original_client
