import base64
import datetime as dt

import pytest
import sqlalchemy as sa

from app.domain.admin_audit.db_models import AdminAuditLog
from app.domain.bookings.db_models import Booking, Team
from app.domain.bookings.service import ensure_default_team
from app.domain.leads.db_models import Lead
from app.domain.workers.db_models import Worker
from app.settings import settings


def _basic_auth(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture(autouse=True)
def _reset_dispatch_creds():
    original = {
        "dispatcher_basic_username": settings.dispatcher_basic_username,
        "dispatcher_basic_password": settings.dispatcher_basic_password,
    }
    settings.dispatcher_basic_username = "dispatch"
    settings.dispatcher_basic_password = "secret"
    yield
    for key, value in original.items():
        setattr(settings, key, value)


async def _seed_booking(async_session_maker, *, team_id: int) -> tuple[str, str, dt.date]:
    async with async_session_maker() as session:
        lead = Lead(
            name="Dispatch Lead",
            phone="+1 555-123-1234",
            email="dispatch@example.com",
            postal_code="12345",
            address="123 Dispatch Way",
            preferred_dates=["Mon"],
            structured_inputs={"beds": 1, "baths": 1, "cleaning_type": "standard"},
            estimate_snapshot={
                "price_cents": 10000,
                "subtotal_cents": 10000,
                "tax_cents": 0,
                "pricing_config_version": "v1",
                "config_hash": "dispatch",
                "line_items": [],
            },
            pricing_config_version="v1",
            config_hash="dispatch",
        )
        session.add(lead)
        await session.flush()

        booking = Booking(
            team_id=team_id,
            lead_id=lead.lead_id,
            starts_at=dt.datetime.now(tz=dt.timezone.utc) + dt.timedelta(days=1),
            duration_minutes=90,
            status="PENDING",
        )
        session.add(booking)
        await session.commit()
        return booking.booking_id, lead.name or "", booking.starts_at.date()


@pytest.mark.anyio
async def test_admin_can_create_and_update_workers(client, async_session_maker):
    async with async_session_maker() as session:
        team = await ensure_default_team(session)
        await session.commit()
        team_id = team.team_id

    headers = _basic_auth("dispatch", "secret")
    create_resp = client.post(
        "/v1/admin/ui/workers/new",
        headers=headers,
        data={
            "name": "Test Worker",
            "phone": "+1 555-8888",
            "email": "worker@demo.com",
            "team_id": team_id,
            "role": "Cleaner",
            "hourly_rate_cents": "2500",
            "is_active": "on",
        },
        follow_redirects=False,
    )
    assert create_resp.status_code == 303

    async with async_session_maker() as session:
        worker = (await session.execute(sa.select(Worker))).scalars().first()
        assert worker is not None
        assert worker.name == "Test Worker"
        assert worker.team_id == team_id

        update_resp = client.post(
            f"/v1/admin/ui/workers/{worker.worker_id}",
            headers=headers,
            data={
                "name": "Updated Worker",
                "phone": worker.phone,
                "team_id": worker.team_id,
                "hourly_rate_cents": "3000",
            },
            follow_redirects=False,
        )
        assert update_resp.status_code == 303
        await session.refresh(worker)
        assert worker.name == "Updated Worker"
        assert worker.hourly_rate_cents == 3000
        assert worker.is_active is True

    list_resp = client.get("/v1/admin/ui/workers?active_only=1", headers=headers)
    assert list_resp.status_code == 200
    assert "Updated Worker" in list_resp.text


@pytest.mark.anyio
async def test_dispatch_assigns_worker_and_writes_audit(client, async_session_maker):
    async with async_session_maker() as session:
        team = await ensure_default_team(session)
        await session.commit()
        team_id = team.team_id

    headers = _basic_auth("dispatch", "secret")
    async with async_session_maker() as session:
        other_team_row = Team(name="Crew B")
        session.add(other_team_row)
        await session.flush()

        worker = Worker(name="Assign Me", phone="+1 555-0000", team_id=team_id, email="a@example.com", is_active=True)
        other_team = Worker(name="Other Team", phone="+1 555-1111", team_id=other_team_row.team_id, email="b@example.com", is_active=True)
        session.add_all([worker, other_team])
        await session.flush()
        booking_id, lead_name, booking_date = await _seed_booking(async_session_maker, team_id=team_id)
        await session.commit()

    dispatch_resp = client.get(
        f"/v1/admin/ui/dispatch?date={booking_date.isoformat()}",
        headers=headers,
    )
    assert dispatch_resp.status_code == 200
    assert lead_name in dispatch_resp.text

    assign_resp = client.post(
        "/v1/admin/ui/dispatch/assign",
        headers=headers,
        data={"booking_id": booking_id, "worker_id": worker.worker_id},
        follow_redirects=False,
    )
    assert assign_resp.status_code == 303

    async with async_session_maker() as verify:
        booking = await verify.get(Booking, booking_id)
        assert booking is not None
        assert booking.assigned_worker_id == worker.worker_id

        logs = (await verify.execute(sa.select(AdminAuditLog))).scalars().all()
        assert any(log.action == "ASSIGN_WORKER" and log.resource_id == booking_id for log in logs)

    cross_resp = client.post(
        "/v1/admin/ui/dispatch/assign",
        headers=headers,
        data={"booking_id": booking_id, "worker_id": other_team.worker_id},
        follow_redirects=False,
    )
    assert cross_resp.status_code == 400

    async with async_session_maker() as final_verify:
        booking = await final_verify.get(Booking, booking_id)
        assert booking is not None
        assert booking.assigned_worker_id == worker.worker_id


@pytest.mark.anyio
async def test_dispatch_can_unassign_worker_with_blank_value(client, async_session_maker):
    async with async_session_maker() as session:
        team = await ensure_default_team(session)
        await session.commit()
        team_id = team.team_id

    headers = _basic_auth("dispatch", "secret")
    async with async_session_maker() as session:
        worker = Worker(name="Assign Me", phone="+1 555-0000", team_id=team_id, email="a@example.com", is_active=True)
        session.add(worker)
        await session.flush()
        booking_id, _lead_name, _booking_date = await _seed_booking(async_session_maker, team_id=team_id)
        await session.commit()

    # Assign first
    assign_resp = client.post(
        "/v1/admin/ui/dispatch/assign",
        headers=headers,
        data={"booking_id": booking_id, "worker_id": worker.worker_id},
        follow_redirects=False,
    )
    assert assign_resp.status_code == 303

    # Unassign with blank worker_id
    unassign_resp = client.post(
        "/v1/admin/ui/dispatch/assign",
        headers=headers,
        data={"booking_id": booking_id, "worker_id": ""},
        follow_redirects=False,
    )
    assert unassign_resp.status_code == 303

    async with async_session_maker() as verify:
        booking = await verify.get(Booking, booking_id)
        assert booking is not None
        assert booking.assigned_worker_id is None
