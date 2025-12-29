import asyncio
import base64
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.leads.db_models import Lead
from app.domain.bookings.service import LOCAL_TZ
from app.settings import settings


def _basic_auth_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_admin_leads_requires_auth(client):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"

    response = client.get("/v1/admin/leads")
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Basic"

    auth_headers = _basic_auth_header("admin", "secret")
    authorized = client.get("/v1/admin/leads", headers=auth_headers)
    assert authorized.status_code == 200
    assert isinstance(authorized.json(), list)


def test_admin_cleanup_removes_old_pending_bookings(client, async_session_maker):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"

    async def _seed() -> None:
        from datetime import datetime, timedelta, timezone

        from app.domain.bookings.db_models import Booking

        async with async_session_maker() as session:
            old_booking = Booking(
                team_id=1,
                starts_at=datetime.now(tz=timezone.utc) - timedelta(days=1),
                duration_minutes=60,
                status="PENDING",
                created_at=datetime.now(tz=timezone.utc) - timedelta(hours=2),
            )
            fresh_booking = Booking(
                team_id=1,
                starts_at=datetime.now(tz=timezone.utc) + timedelta(hours=2),
                duration_minutes=60,
                status="PENDING",
                created_at=datetime.now(tz=timezone.utc),
            )
            session.add_all([old_booking, fresh_booking])
            await session.commit()

    import asyncio

    asyncio.run(_seed())

    headers = _basic_auth_header("admin", "secret")
    response = client.post("/v1/admin/cleanup", headers=headers)
    assert response.status_code == 202
    assert response.json()["deleted"] == 1


def test_admin_auth_missing_config_returns_401(client):
    settings.admin_basic_username = None
    settings.admin_basic_password = None

    response = client.get("/v1/admin/leads")
    assert response.status_code == 401


def _create_lead(client) -> str:
    estimate_response = client.post(
        "/v1/estimate",
        json={
            "beds": 1,
            "baths": 1,
            "cleaning_type": "standard",
            "heavy_grease": False,
            "multi_floor": False,
            "frequency": "one_time",
            "add_ons": {},
        },
    )
    assert estimate_response.status_code == 200
    payload = {
        "name": "Admin Test",
        "phone": "780-555-0101",
        "preferred_dates": [],
        "structured_inputs": {"beds": 1, "baths": 1, "cleaning_type": "standard"},
        "estimate_snapshot": estimate_response.json(),
    }
    lead_response = client.post("/v1/leads", json=payload)
    assert lead_response.status_code == 201
    return lead_response.json()["lead_id"]


def _create_booking(client, lead_id: str | None = None, days_ahead: int = 1) -> str:
    starts_at_local = (
        datetime.now(tz=LOCAL_TZ)
        .replace(hour=10, minute=0, second=0, microsecond=0)
        + timedelta(days=days_ahead)
    )
    if starts_at_local.weekday() >= 5:
        starts_at_local += timedelta(days=(7 - starts_at_local.weekday()))
    payload = {
        "starts_at": starts_at_local.astimezone(timezone.utc).isoformat(),
        "time_on_site_hours": 1,
        "lead_id": lead_id,
    }
    response = client.post("/v1/bookings", json=payload)
    assert response.status_code == 201
    return response.json()["booking_id"]


def test_admin_updates_lead_status_with_valid_transition(client, async_session_maker):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"

    lead_id = _create_lead(client)
    headers = _basic_auth_header("admin", "secret")

    transition = client.post(
        f"/v1/admin/leads/{lead_id}/status",
        headers=headers,
        json={"status": "CONTACTED"},
    )
    assert transition.status_code == 200
    assert transition.json()["status"] == "CONTACTED"

    filtered = client.get("/v1/admin/leads", headers=headers, params={"status": "CONTACTED"})
    assert filtered.status_code == 200
    assert any(lead["lead_id"] == lead_id for lead in filtered.json())

    async def _fetch_status() -> str:
        async with async_session_maker() as session:
            lead = await session.get(Lead, lead_id)
            assert lead
            return lead.status

    assert asyncio.run(_fetch_status()) == "CONTACTED"

    invalid = client.post(
        f"/v1/admin/leads/{lead_id}/status",
        headers=headers,
        json={"status": "DONE"},
    )
    assert invalid.status_code == 400


def test_dispatcher_can_manage_bookings_but_not_pricing(client, async_session_maker):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"
    settings.dispatcher_basic_username = "dispatcher"
    settings.dispatcher_basic_password = "dispatch-secret"
    original_stripe_key = settings.stripe_secret_key
    settings.stripe_secret_key = "sk_test_key"
    original_deposit_percent = settings.deposit_percent
    settings.deposit_percent = 0

    try:
        lead_id = _create_lead(client)
        booking_id = _create_booking(client, lead_id=lead_id)

        dispatcher_headers = _basic_auth_header("dispatcher", "dispatch-secret")
        leads_response = client.get("/v1/admin/leads", headers=dispatcher_headers)
        assert leads_response.status_code == 200

        update = client.post(
            f"/v1/admin/leads/{lead_id}/status",
            headers=dispatcher_headers,
            json={"status": "CONTACTED"},
        )
        assert update.status_code == 200

        confirm = client.post(f"/v1/admin/bookings/{booking_id}/confirm", headers=dispatcher_headers)
        assert confirm.status_code == 200
        assert confirm.json()["status"] == "CONFIRMED"

        reschedule_payload = {
            "starts_at": (
                datetime.now(tz=LOCAL_TZ)
                .replace(hour=11, minute=0, second=0, microsecond=0)
                .astimezone(timezone.utc)
                .isoformat()
            ),
            "time_on_site_hours": 1.5,
        }
        reschedule = client.post(
            f"/v1/admin/bookings/{booking_id}/reschedule", headers=dispatcher_headers, json=reschedule_payload
        )
        assert reschedule.status_code == 200

        cancel = client.post(f"/v1/admin/bookings/{booking_id}/cancel", headers=dispatcher_headers)
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "CANCELLED"

        pricing_attempt = client.post("/v1/admin/pricing/reload", headers=dispatcher_headers)
        assert pricing_attempt.status_code == 403

        admin_headers = _basic_auth_header("admin", "secret")
        admin_pricing = client.post("/v1/admin/pricing/reload", headers=admin_headers)
        assert admin_pricing.status_code == 202
    finally:
        settings.stripe_secret_key = original_stripe_key
        settings.deposit_percent = original_deposit_percent
