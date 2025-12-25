import asyncio
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from app.domain.bookings.db_models import Booking
from app.domain.leads.db_models import ReferralRedemption
from app.settings import settings


def _future_start(days_ahead: int) -> str:
    target_day = datetime.now(tz=timezone.utc) + timedelta(days=days_ahead)
    start = target_day.replace(hour=10, minute=0, second=0, microsecond=0)
    return start.isoformat()


def _create_estimate(client):
    response = client.post(
        "/v1/estimate",
        json={
            "beds": 2,
            "baths": 1,
            "cleaning_type": "standard",
            "heavy_grease": False,
            "multi_floor": False,
            "frequency": "one_time",
            "add_ons": {},
        },
    )
    assert response.status_code == 200
    return response.json()


def _create_lead(client, estimate, name: str):
    response = client.post(
        "/v1/leads",
        json={
            "name": name,
            "phone": "780-555-1212",
            "email": f"{name.lower().replace(' ', '.')}@example.com",
            "preferred_dates": ["Mon"],
            "structured_inputs": {"beds": 2, "baths": 1, "cleaning_type": "standard"},
            "estimate_snapshot": estimate,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_lead_response_includes_referral_code(client):
    estimate = _create_estimate(client)
    lead = _create_lead(client, estimate, "Referral Owner")
    assert lead["referral_code"]
    assert lead["lead_id"]


def test_referral_credit_applied_once_per_lead(client, async_session_maker):
    estimate = _create_estimate(client)
    original_percent = settings.deposit_percent
    settings.deposit_percent = 0
    try:
        referrer = _create_lead(client, estimate, "Referrer One")
        referred = _create_lead(client, estimate, "Referred Friend")

        payload = {
            "starts_at": _future_start(1),
            "time_on_site_hours": 2.0,
            "lead_id": referred["lead_id"],
            "referral_code": referrer["referral_code"],
        }
        first_booking = client.post("/v1/bookings", json=payload)
        assert first_booking.status_code == 201, first_booking.text
        booking_one = first_booking.json()
        assert booking_one["referral_code_applied"] == referrer["referral_code"]
        assert booking_one["referral_credit_cents"] == settings.referral_credit_cents

        payload["starts_at"] = _future_start(2)
        second_booking = client.post("/v1/bookings", json=payload)
        assert second_booking.status_code == 201, second_booking.text
        booking_two = second_booking.json()
        assert booking_two["referral_credit_cents"] == 0

        async def _fetch_counts():
            async with async_session_maker() as session:
                redemptions = await session.execute(
                    sa.select(sa.func.count()).select_from(ReferralRedemption)
                )
                first = await session.get(Booking, booking_one["booking_id"])
                second = await session.get(Booking, booking_two["booking_id"])
                return int(redemptions.scalar_one()), first.referral_credit_cents, second.referral_credit_cents

        redemption_count, booking_one_credit, booking_two_credit = asyncio.run(_fetch_counts())
        assert redemption_count == 1
        assert booking_one_credit == settings.referral_credit_cents
        assert booking_two_credit == 0
    finally:
        settings.deposit_percent = original_percent
