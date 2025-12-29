import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import stripe
import sqlalchemy as sa

from app.domain.bookings.db_models import Booking, EmailEvent
from app.domain.leads.db_models import Lead
from app.infra import stripe as stripe_infra
from app.main import app
from app.settings import settings

LOCAL_TZ = ZoneInfo("America/Edmonton")


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


def _seed_returning_lead(async_session_maker) -> str:
    async def _create() -> str:
        async with async_session_maker() as session:
            lead = Lead(
                name="Returning Lead",
                phone="780-555-1111",
                email="returning@example.com",
                postal_code="T5B",
                preferred_dates=["Fri"],
                structured_inputs={"beds": 1, "baths": 1, "cleaning_type": "standard"},
                estimate_snapshot={
                    "pricing_config_version": "v1",
                    "config_hash": "hash",
                    "total_before_tax": 120.0,
                },
                pricing_config_version="v1",
                config_hash="hash",
                status="NEW",
            )
            session.add(lead)
            await session.flush()

            booking = Booking(
                team_id=1,
                lead_id=lead.lead_id,
                starts_at=datetime.now(tz=timezone.utc),
                duration_minutes=60,
                status="CONFIRMED",
            )
            session.add(booking)
            await session.commit()
            await session.refresh(lead)
            return lead.lead_id

    import asyncio

    return asyncio.run(_create())


def _booking_start_in_days(days: int, hour: int = 10) -> str:
    now_local = datetime.now(tz=LOCAL_TZ)
    target_local = (now_local + timedelta(days=days)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    return target_local.astimezone(timezone.utc).isoformat()


def _booking_start_in_hours(hours: int) -> str:
    now_local = datetime.now(tz=LOCAL_TZ)
    target_local = (now_local + timedelta(hours=hours)).replace(minute=0, second=0, microsecond=0)
    if target_local.hour < 9 or target_local.hour >= 18:
        target_local = target_local.replace(hour=10, minute=0, second=0, microsecond=0)
    return target_local.astimezone(timezone.utc).isoformat()


def _count_bookings(async_session_maker) -> int:
    async def _count() -> int:
        async with async_session_maker() as session:
            result = await session.execute(sa.select(sa.func.count()).select_from(Booking))
            return int(result.scalar_one())

    import asyncio

    return asyncio.run(_count())


def _count_email_events(async_session_maker) -> int:
    async def _count() -> int:
        async with async_session_maker() as session:
            result = await session.execute(sa.select(sa.func.count()).select_from(EmailEvent))
            return int(result.scalar_one())

    import asyncio

    return asyncio.run(_count())


class RecordingAdapter:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    async def send_email(self, recipient: str, subject: str, body: str) -> bool:
        self.sent.append((recipient, subject, body))
        return True


def test_booking_response_includes_deposit_policy(client, async_session_maker, monkeypatch):
    settings.stripe_secret_key = "sk_test"
    settings.stripe_webhook_secret = "whsec_test"
    original_client = getattr(app.state, "stripe_client", None)
    try:
        app.state.stripe_client = _stub_stripe("cs_test_deposit")
        lead_id = _seed_lead(async_session_maker)

        payload = {
            "starts_at": _booking_start_in_days(5),
            "time_on_site_hours": 2,
            "lead_id": lead_id,
        }
        response = client.post("/v1/bookings", json=payload)
        assert response.status_code == 201, response.text
        data = response.json()
        assert data["deposit_required"] is True
        assert data["deposit_cents"] == 7000
        assert set(data["deposit_policy"]) >= {"service_type_deep", "first_time_client"}
        snapshot = data["policy_snapshot"]
        assert snapshot["deposit"]["basis"] == "percent_clamped"
        assert snapshot["cancellation"]["windows"][0]["start_hours_before"] == 72.0
        assert data["checkout_url"] == "https://example.com/checkout"
        assert data["deposit_status"] == "pending"
    finally:
        app.state.stripe_client = original_client


def test_missing_stripe_key_does_not_create_booking(client, async_session_maker):
    original_secret = settings.stripe_secret_key
    settings.stripe_secret_key = None
    adapter = RecordingAdapter()
    original_adapter = getattr(app.state, "email_adapter", None)
    app.state.email_adapter = adapter

    lead_id = _seed_lead(async_session_maker)
    payload = {
        "starts_at": _booking_start_in_days(4),
        "time_on_site_hours": 2,
        "lead_id": lead_id,
    }

    try:
        response = client.post("/v1/bookings", json=payload)
        assert response.status_code == 503
        assert _count_bookings(async_session_maker) == 0
        assert _count_email_events(async_session_maker) == 0
        assert adapter.sent == []
    finally:
        app.state.email_adapter = original_adapter
        settings.stripe_secret_key = original_secret


def test_checkout_failure_rolls_back_booking(client, async_session_maker, monkeypatch):
    original_secret = settings.stripe_secret_key
    settings.stripe_secret_key = "sk_test"
    adapter = RecordingAdapter()
    original_adapter = getattr(app.state, "email_adapter", None)
    app.state.email_adapter = adapter

    def _raise(**_: object) -> None:
        raise RuntimeError("stripe_down")

    monkeypatch.setattr(stripe_infra, "create_checkout_session", _raise)
    lead_id = _seed_lead(async_session_maker)
    payload = {
        "starts_at": _booking_start_in_days(4),
        "time_on_site_hours": 2,
        "lead_id": lead_id,
    }

    try:
        response = client.post("/v1/bookings", json=payload)
        assert response.status_code == 503
        assert _count_bookings(async_session_maker) == 0
        assert _count_email_events(async_session_maker) == 0
        assert adapter.sent == []
    finally:
        app.state.email_adapter = original_adapter
        settings.stripe_secret_key = original_secret


def test_non_deposit_booking_persists(client, async_session_maker):
    original_secret = settings.stripe_secret_key
    settings.stripe_secret_key = None
    payload = {"starts_at": _booking_start_in_days(5), "time_on_site_hours": 1}

    try:
        response = client.post("/v1/bookings", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["deposit_required"] is False
        assert _count_bookings(async_session_maker) == 1
    finally:
        settings.stripe_secret_key = original_secret


def test_returning_client_can_book_without_deposit(client, async_session_maker):
    settings.stripe_secret_key = None
    lead_id = _seed_returning_lead(async_session_maker)
    starts_at = _booking_start_in_days(6)

    response = client.post(
        "/v1/bookings",
        json={"starts_at": starts_at, "time_on_site_hours": 1.0, "lead_id": lead_id},
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["deposit_required"] is False
    assert data["deposit_policy"] == []
    assert data["policy_snapshot"]["deposit"]["required"] is False


def test_short_notice_policy_snapshot(client, async_session_maker):
    settings.stripe_secret_key = "sk_test"
    settings.stripe_webhook_secret = "whsec_test"
    original_client = getattr(app.state, "stripe_client", None)
    try:
        app.state.stripe_client = _stub_stripe("cs_short_notice")
        lead_id = _seed_lead(async_session_maker)
        payload = {
            "starts_at": _booking_start_in_hours(12),
            "time_on_site_hours": 2,
            "lead_id": lead_id,
        }
        response = client.post("/v1/bookings", json=payload)
        assert response.status_code == 201, response.text
        data = response.json()
        assert data["deposit_required"] is True
        assert "short_notice" in data["deposit_policy"]
        snapshot = data["policy_snapshot"]
        assert snapshot["lead_time_hours"] < 24
        assert "short_notice" in snapshot["cancellation"]["rules"]
        partial_window = next(w for w in snapshot["cancellation"]["windows"] if w["label"] == "partial")
        assert partial_window["refund_percent"] == 25

        async def _fetch() -> dict:
            async with async_session_maker() as session:
                result = await session.execute(sa.select(Booking).order_by(Booking.created_at.desc()).limit(1))
                booking = result.scalar_one()
                return booking.policy_snapshot or {}

        import asyncio

        stored_snapshot = asyncio.run(_fetch())
        assert stored_snapshot.get("deposit", {}).get("amount_cents") == data["deposit_cents"]
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
            "starts_at": _booking_start_in_days(4),
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

        webhook_response = client.post("/v1/stripe/webhook", content=body, headers=headers)
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


def test_webhook_requires_paid_status(client, async_session_maker):
    settings.stripe_secret_key = "sk_test"
    settings.stripe_webhook_secret = "whsec_test"
    original_client = getattr(app.state, "stripe_client", None)
    try:
        app.state.stripe_client = _stub_stripe("cs_unpaid")
        lead_id = _seed_lead(async_session_maker)

        creation = client.post(
            "/v1/bookings",
            json={"starts_at": _booking_start_in_days(4), "time_on_site_hours": 2, "lead_id": lead_id},
        )
        assert creation.status_code == 201

        event = {
            "id": "evt_unpaid",
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_unpaid", "payment_intent": "pi_unpaid", "payment_status": "unpaid"}},
        }
        body = json.dumps(event)
        timestamp = int(time.time())
        signed_payload = f"{timestamp}.{body}"
        signature = stripe.WebhookSignature._compute_signature(signed_payload, settings.stripe_webhook_secret)
        headers = {"Stripe-Signature": f"t={timestamp},v1={signature}"}

        webhook_response = client.post("/v1/stripe/webhook", content=body, headers=headers)
        assert webhook_response.status_code == 200

        async def _fetch() -> Booking:
            async with async_session_maker() as session:
                result = await session.execute(sa.select(Booking).limit(1))
                return result.scalar_one()

        import asyncio

        booking = asyncio.run(_fetch())
        assert booking.status == "PENDING"
        assert booking.deposit_status == "pending"
    finally:
        app.state.stripe_client = original_client


def test_webhook_expired_cancels_pending(client, async_session_maker):
    settings.stripe_secret_key = "sk_test"
    settings.stripe_webhook_secret = "whsec_test"
    original_client = getattr(app.state, "stripe_client", None)
    try:
        app.state.stripe_client = _stub_stripe("cs_expired")
        lead_id = _seed_lead(async_session_maker)

        creation = client.post(
            "/v1/bookings",
            json={"starts_at": _booking_start_in_days(4), "time_on_site_hours": 2, "lead_id": lead_id},
        )
        assert creation.status_code == 201

        event = {
            "id": "evt_expired",
            "type": "checkout.session.expired",
            "data": {"object": {"id": "cs_expired", "payment_intent": "pi_expired"}},
        }
        body = json.dumps(event)
        timestamp = int(time.time())
        signed_payload = f"{timestamp}.{body}"
        signature = stripe.WebhookSignature._compute_signature(signed_payload, settings.stripe_webhook_secret)
        headers = {"Stripe-Signature": f"t={timestamp},v1={signature}"}

        webhook_response = client.post("/v1/stripe/webhook", content=body, headers=headers)
        assert webhook_response.status_code == 200

        async def _fetch() -> Booking:
            async with async_session_maker() as session:
                result = await session.execute(sa.select(Booking).limit(1))
                return result.scalar_one()

        import asyncio

        booking = asyncio.run(_fetch())
        assert booking.status == "CANCELLED"
        assert booking.deposit_status == "expired"
    finally:
        app.state.stripe_client = original_client
