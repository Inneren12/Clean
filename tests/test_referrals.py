import asyncio

from sqlalchemy import select

import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.domain.bookings import service as booking_service
from app.domain.leads.db_models import Lead, ReferralCredit
from app.settings import settings


def _next_available_start() -> datetime:
    local_tz = ZoneInfo("America/Edmonton")
    start_time_local = datetime.now(tz=local_tz).replace(
        hour=10, minute=0, second=0, microsecond=0
    ) + timedelta(days=2)
    while start_time_local.weekday() >= 5:
        start_time_local += timedelta(days=1)
    return start_time_local.astimezone(timezone.utc)


def _make_estimate(client):
    response = client.post(
        "/v1/estimate",
        json={
            "beds": 2,
            "baths": 2,
            "cleaning_type": "deep",
            "heavy_grease": False,
            "multi_floor": False,
            "frequency": "one_time",
            "add_ons": {},
        },
    )
    assert response.status_code == 200
    return response.json()


def test_referral_credit_created_after_confirmation(client, async_session_maker):
    estimate = _make_estimate(client)

    referrer_payload = {
        "name": "Referrer",
        "phone": "780-555-1111",
        "preferred_dates": [],
        "structured_inputs": {"beds": 2, "baths": 2, "cleaning_type": "deep"},
        "estimate_snapshot": estimate,
    }
    referrer_response = client.post("/v1/leads", json=referrer_payload)
    assert referrer_response.status_code == 201
    referral_code = referrer_response.json()["referral_code"]

    referred_payload = {
        "name": "New Client",
        "phone": "780-555-2222",
        "preferred_dates": [],
        "structured_inputs": {"beds": 2, "baths": 2, "cleaning_type": "deep"},
        "estimate_snapshot": estimate,
        "referral_code": referral_code,
    }
    referred_response = client.post("/v1/leads", json=referred_payload)
    assert referred_response.status_code == 201
    referred_id = referred_response.json()["lead_id"]

    async def _fetch_state():
        async with async_session_maker() as session:
            referrer = await session.get(Lead, referrer_response.json()["lead_id"])
            referred = await session.get(Lead, referred_id)
            credit_count = await session.scalar(
                select(func.count()).select_from(ReferralCredit)
            )
            return referrer, referred, credit_count

    referrer, referred, credit_count = asyncio.run(_fetch_state())
    assert referred.referred_by_code == referral_code
    assert credit_count == 0

    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"
    starts_at = _next_available_start()

    async def _create_booking() -> str:
        async with async_session_maker() as session:
            booking = await booking_service.create_booking(
                starts_at=starts_at,
                duration_minutes=120,
                lead_id=referred_id,
                session=session,
                deposit_decision=booking_service.DepositDecision(
                    required=False, reasons=[], deposit_cents=None
                ),
                manage_transaction=True,
            )
            return booking.booking_id

    booking_id = asyncio.run(_create_booking())

    auth = (settings.admin_basic_username, settings.admin_basic_password)
    confirm_response = client.post(
        f"/v1/admin/bookings/{booking_id}/confirm", auth=auth
    )
    assert confirm_response.status_code == 200

    confirm_repeat = client.post(
        f"/v1/admin/bookings/{booking_id}/confirm", auth=auth
    )
    assert confirm_repeat.status_code == 200

    async def _fetch_credit_count():
        async with async_session_maker() as session:
            return await session.scalar(select(func.count()).select_from(ReferralCredit))

    credit_count_after = asyncio.run(_fetch_credit_count())
    assert credit_count_after == 1


def test_invalid_referral_code_rejected(client):
    estimate = _make_estimate(client)
    payload = {
        "name": "Bad Code",
        "phone": "780-555-9999",
        "preferred_dates": [],
        "structured_inputs": {"beds": 2, "baths": 2, "cleaning_type": "deep"},
        "estimate_snapshot": estimate,
        "referral_code": "INVALID",
    }

    response = client.post("/v1/leads", json=payload)
    assert response.status_code == 400


def test_referral_credit_created_on_deposit_paid(client, async_session_maker):
    estimate = _make_estimate(client)

    referrer_response = client.post(
        "/v1/leads",
        json={
            "name": "Deposit Referrer",
            "phone": "780-555-7777",
            "preferred_dates": [],
            "structured_inputs": {"beds": 2, "baths": 2, "cleaning_type": "deep"},
            "estimate_snapshot": estimate,
        },
    )
    assert referrer_response.status_code == 201
    referral_code = referrer_response.json()["referral_code"]

    referred_response = client.post(
        "/v1/leads",
        json={
            "name": "Deposit Referred",
            "phone": "780-555-8888",
            "preferred_dates": [],
            "structured_inputs": {"beds": 2, "baths": 2, "cleaning_type": "deep"},
            "estimate_snapshot": estimate,
            "referral_code": referral_code,
        },
    )
    assert referred_response.status_code == 201
    referred_id = referred_response.json()["lead_id"]

    async def _create_deposit_booking() -> str:
        async with async_session_maker() as session:
            booking = await booking_service.create_booking(
                starts_at=_next_available_start(),
                duration_minutes=180,
                lead_id=referred_id,
                session=session,
                deposit_decision=booking_service.DepositDecision(
                    required=True, reasons=["new_client"], deposit_cents=5000
                ),
                manage_transaction=True,
            )
            await booking_service.attach_checkout_session(
                session,
                booking.booking_id,
                "cs_test",
                payment_intent_id="pi_test",
                commit=True,
            )
            return booking.booking_id

    booking_id = asyncio.run(_create_deposit_booking())

    async def _mark_paid_and_count():
        async with async_session_maker() as session:
            await booking_service.mark_deposit_paid(
                session=session,
                checkout_session_id="cs_test",
                payment_intent_id="pi_test",
                email_adapter=None,
            )
            credit_count = await session.scalar(
                select(func.count()).select_from(ReferralCredit)
            )
            return credit_count

    credit_count_after = asyncio.run(_mark_paid_and_count())
    assert credit_count_after == 1

def test_admin_lists_referral_metadata(client, async_session_maker):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"
    estimate = _make_estimate(client)

    referrer_response = client.post(
        "/v1/leads",
        json={
            "name": "Admin Referrer",
            "phone": "780-555-3333",
            "preferred_dates": [],
            "structured_inputs": {"beds": 2, "baths": 2, "cleaning_type": "deep"},
            "estimate_snapshot": estimate,
        },
    )
    assert referrer_response.status_code == 201
    referral_code = referrer_response.json()["referral_code"]

    referred_response = client.post(
        "/v1/leads",
        json={
            "name": "Admin Referred",
            "phone": "780-555-4444",
            "preferred_dates": [],
            "structured_inputs": {"beds": 2, "baths": 2, "cleaning_type": "deep"},
            "estimate_snapshot": estimate,
            "referral_code": referral_code,
        },
    )
    assert referred_response.status_code == 201

    starts_at = _next_available_start()

    async def _create_booking() -> str:
        async with async_session_maker() as session:
            booking = await booking_service.create_booking(
                starts_at=starts_at,
                duration_minutes=120,
                lead_id=referred_response.json()["lead_id"],
                session=session,
                deposit_decision=booking_service.DepositDecision(
                    required=False, reasons=[], deposit_cents=None
                ),
                manage_transaction=True,
            )
            return booking.booking_id

    booking_id = asyncio.run(_create_booking())
    auth = (settings.admin_basic_username, settings.admin_basic_password)
    confirm_response = client.post(
        f"/v1/admin/bookings/{booking_id}/confirm", auth=auth
    )
    assert confirm_response.status_code == 200

    auth = (settings.admin_basic_username, settings.admin_basic_password)
    leads = client.get("/v1/admin/leads", auth=auth)
    assert leads.status_code == 200
    payload = leads.json()
    assert any(entry["referral_code"] == referral_code for entry in payload)
    referrer_entry = next(entry for entry in payload if entry["referral_code"] == referral_code)
    assert referrer_entry["referral_credits"] == 1
