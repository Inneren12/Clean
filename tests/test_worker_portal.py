import base64
import datetime
from datetime import timezone

import pytest
import sqlalchemy as sa

from app.domain.admin_audit.db_models import AdminAuditLog
from app.domain.bookings.db_models import Booking
from app.domain.leads.db_models import Lead
from app.settings import settings


def _basic_auth(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


async def _seed_booking(async_session_maker, *, team_id: int = 1) -> str:
    async with async_session_maker() as session:
        lead = Lead(
            name="Worker Lead",
            phone="780-555-9999",
            email="worker@example.com",
            postal_code="T5A",
            address="55 Field Ave",
            preferred_dates=["Mon"],
            structured_inputs={"beds": 2, "baths": 1, "cleaning_type": "standard"},
            estimate_snapshot={
                "price_cents": 12000,
                "subtotal_cents": 12000,
                "tax_cents": 0,
                "pricing_config_version": "v1",
                "config_hash": "hash",
                "line_items": [],
            },
            pricing_config_version="v1",
            config_hash="hash",
        )
        session.add(lead)
        await session.flush()
        booking = Booking(
            team_id=team_id,
            lead_id=lead.lead_id,
            starts_at=datetime.datetime.now(tz=timezone.utc) + datetime.timedelta(hours=1),
            duration_minutes=90,
            status="PENDING",
            deposit_required=True,
            deposit_status="pending",
            risk_band="MEDIUM",
            risk_reasons=["Large job"],
        )
        session.add(booking)
        await session.commit()
        await session.refresh(booking)
        return booking.booking_id


@pytest.mark.anyio
async def test_worker_portal_dashboard_lists_jobs(client, async_session_maker):
    settings.worker_basic_username = "worker"
    settings.worker_basic_password = "secret"
    settings.worker_team_id = 1
    booking_id = await _seed_booking(async_session_maker, team_id=1)

    login = client.post("/worker/login", headers=_basic_auth("worker", "secret"))
    assert login.status_code == 200

    resp = client.get("/worker")
    assert resp.status_code == 200
    assert booking_id in resp.text

    jobs_resp = client.get("/worker/jobs")
    assert jobs_resp.status_code == 200
    assert "Deposit" in jobs_resp.text

    async with async_session_maker() as session:
        logs = (await session.execute(sa.select(AdminAuditLog))).scalars().all()
        assert any(log.action == "VIEW_DASHBOARD" for log in logs)


@pytest.mark.anyio
async def test_worker_cannot_view_other_team(client, async_session_maker):
    settings.worker_basic_username = "worker"
    settings.worker_basic_password = "secret"
    settings.worker_team_id = 1
    other_booking = await _seed_booking(async_session_maker, team_id=2)

    client.post("/worker/login", headers=_basic_auth("worker", "secret"))
    detail = client.get(f"/worker/jobs/{other_booking}")
    assert detail.status_code == 404


@pytest.mark.anyio
async def test_admin_cannot_access_worker_portal_without_worker_login(client_no_raise, async_session_maker):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"
    settings.worker_basic_username = "worker"
    settings.worker_basic_password = "secret"
    settings.worker_team_id = 1
    await _seed_booking(async_session_maker, team_id=1)

    resp = client_no_raise.get("/worker")
    assert resp.status_code == 401

