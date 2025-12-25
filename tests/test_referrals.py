import asyncio

from sqlalchemy import select

from app.domain.leads.db_models import Lead, ReferralCredit
from app.settings import settings


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


def test_referral_credit_created_for_new_lead(client, async_session_maker):
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
            credits = (
                await session.execute(
                    select(ReferralCredit).where(
                        ReferralCredit.referrer_lead_id == referrer.lead_id
                    )
                )
            ).scalars().all()
            return referrer, referred, credits

    referrer, referred, credits = asyncio.run(_fetch_state())
    assert referred.referred_by_code == referral_code
    assert len(credits) == 1
    assert credits[0].referred_lead_id == referred.lead_id


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


def test_admin_lists_referral_metadata(client):
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

    auth = (settings.admin_basic_username, settings.admin_basic_password)
    leads = client.get("/v1/admin/leads", auth=auth)
    assert leads.status_code == 200
    payload = leads.json()
    assert any(entry["referral_code"] == referral_code for entry in payload)
    referrer_entry = next(entry for entry in payload if entry["referral_code"] == referral_code)
    assert referrer_entry["referral_credits"] == 1
