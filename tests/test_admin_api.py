import asyncio
import base64

from app.settings import settings
from app.domain.leads.db_models import Lead


def _basic_auth_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_admin_leads_requires_auth(client):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"

    response = client.get("/v1/admin/leads")
    assert response.status_code == 401

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
