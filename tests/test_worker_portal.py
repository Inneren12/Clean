import base64
import datetime
from datetime import date, timezone

import pytest
import sqlalchemy as sa

from app.domain.addons.db_models import AddonDefinition
from app.domain.admin_audit.db_models import AdminAuditLog
from app.domain.analytics.db_models import EventLog
from app.domain.bookings.db_models import Booking
from app.domain.invoices import service as invoice_service
from app.domain.invoices.db_models import Invoice
from app.domain.invoices.schemas import InvoiceItemCreate
from app.domain.leads.db_models import Lead
from app.domain.reason_logs.db_models import ReasonLog
from app.domain.time_tracking.db_models import WorkTimeEntry
from app.settings import settings


@pytest.fixture(autouse=True)
def _restore_worker_settings():
    original = {
        "worker_basic_username": settings.worker_basic_username,
        "worker_basic_password": settings.worker_basic_password,
        "worker_team_id": settings.worker_team_id,
        "admin_basic_username": settings.admin_basic_username,
        "admin_basic_password": settings.admin_basic_password,
        "worker_portal_secret": settings.worker_portal_secret,
    }
    settings.worker_portal_secret = "test-worker-secret"
    yield
    for key, value in original.items():
        setattr(settings, key, value)


def test_routes_worker_import():
    import importlib

    importlib.invalidate_caches()
    importlib.import_module("app.api.routes_worker")


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


@pytest.mark.anyio
async def test_worker_tracks_time_with_reasons(client, async_session_maker):
    settings.worker_basic_username = "worker"
    settings.worker_basic_password = "secret"
    settings.worker_team_id = 1
    booking_id = await _seed_booking(async_session_maker, team_id=1)

    client.post("/worker/login", headers=_basic_auth("worker", "secret"))

    start = client.post(f"/worker/jobs/{booking_id}/start")
    assert start.status_code == 200

    pause = client.post(f"/worker/jobs/{booking_id}/pause")
    assert pause.status_code == 200

    resume = client.post(f"/worker/jobs/{booking_id}/resume")
    assert resume.status_code == 200

    finish = client.post(
        f"/worker/jobs/{booking_id}/finish",
        data={
            "delay_reason": "ACCESS_DELAY",
            "delay_note": "Gate code issue",
            "price_adjust_reason": "EXTRA_SERVICE",
            "price_adjust_note": "Added fridge cleaning",
        },
    )
    assert finish.status_code == 200

    async with async_session_maker() as session:
        entry = (
            await session.execute(sa.select(WorkTimeEntry).where(WorkTimeEntry.booking_id == booking_id))
        ).scalar_one()
        booking = await session.get(Booking, booking_id)
        reasons = (
            await session.execute(sa.select(ReasonLog).where(ReasonLog.order_id == booking_id))
        ).scalars().all()
        audits = (
            await session.execute(sa.select(AdminAuditLog).where(AdminAuditLog.resource_id == booking_id))
        ).scalars().all()
        events = (
            await session.execute(sa.select(EventLog).where(EventLog.booking_id == booking_id))
        ).scalars().all()

        assert entry.state == "FINISHED"
        assert entry.worker_id == "worker"
        assert booking.actual_seconds is not None
        assert {reason.kind for reason in reasons} == {"TIME_OVERRUN", "PRICE_ADJUST"}
        assert any(log.action == "WORKER_TIME_UPDATE" for log in audits)
        assert any(event.event_type == "job_time_finished" for event in events)


@pytest.mark.anyio
async def test_worker_cannot_finish_without_start(client, async_session_maker):
    settings.worker_basic_username = "worker"
    settings.worker_basic_password = "secret"
    settings.worker_team_id = 1
    booking_id = await _seed_booking(async_session_maker, team_id=1)

    client.post("/worker/login", headers=_basic_auth("worker", "secret"))
    finish = client.post(f"/worker/jobs/{booking_id}/finish")
    assert finish.status_code == 400


@pytest.mark.anyio
async def test_worker_cannot_mutate_other_team_job(client, async_session_maker):
    settings.worker_basic_username = "worker"
    settings.worker_basic_password = "secret"
    settings.worker_team_id = 1
    other_booking = await _seed_booking(async_session_maker, team_id=2)

    client.post("/worker/login", headers=_basic_auth("worker", "secret"))
    resp = client.post(f"/worker/jobs/{other_booking}/start")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_worker_addons_update_invoice_totals(client, async_session_maker):
    settings.worker_basic_username = "worker"
    settings.worker_basic_password = "secret"
    settings.worker_team_id = 1

    async with async_session_maker() as session:
        lead = Lead(
            name="Invoice Lead",
            phone="780-555-9999",
            email="invoice@example.com",
            postal_code="T5A",
            address="1 Addon St",
            preferred_dates=["Tue"],
            structured_inputs={"beds": 2, "baths": 1, "cleaning_type": "standard"},
            estimate_snapshot={
                "subtotal_cents": 10000,
                "tax_cents": 500,
                "total_before_tax": 10000,
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
            team_id=1,
            lead_id=lead.lead_id,
            starts_at=datetime.datetime.now(tz=timezone.utc) + datetime.timedelta(hours=2),
            duration_minutes=60,
            status="PENDING",
            deposit_required=False,
            deposit_policy=[],
            consent_photos=False,
        )
        session.add(booking)
        addon = AddonDefinition(
            code="WIN",
            name="Windows",
            price_cents=2000,
            default_minutes=30,
            is_active=True,
        )
        session.add(addon)
        await session.flush()

        invoice = await invoice_service.create_invoice_from_order(
            session,
            booking,
            [
                InvoiceItemCreate(
                    description="Base",
                    qty=1,
                    unit_price_cents=10000,
                    tax_rate=0.05,
                )
            ],
            issue_date=date.today(),
        )
        await session.commit()

        booking_id = booking.booking_id
        addon_id = addon.addon_id
        invoice_id = invoice.invoice_id

    client.post("/worker/login", headers=_basic_auth("worker", "secret"))
    resp = client.post(
        f"/worker/jobs/{booking_id}/addons",
        data={"addon_id": addon_id, "qty": 1},
    )
    assert resp.status_code == 200, resp.text

    async with async_session_maker() as session:
        refreshed = await session.get(Invoice, invoice_id)
        assert refreshed is not None
        await session.refresh(refreshed, attribute_names=["items"])
        assert refreshed.subtotal_cents == 12000
        assert refreshed.tax_cents == 600
        assert refreshed.total_cents == 12600
        assert any(item.description == "Windows" for item in refreshed.items)


@pytest.mark.anyio
async def test_worker_cannot_add_addons_to_other_team(client, async_session_maker):
    settings.worker_basic_username = "worker"
    settings.worker_basic_password = "secret"
    settings.worker_team_id = 1

    async with async_session_maker() as session:
        booking = Booking(
            team_id=2,
            starts_at=datetime.datetime.now(tz=timezone.utc) + datetime.timedelta(hours=3),
            duration_minutes=45,
            status="PENDING",
        )
        session.add(booking)
        addon = AddonDefinition(
            code="FRI",
            name="Fridge",
            price_cents=1500,
            default_minutes=20,
            is_active=True,
        )
        session.add(addon)
        await session.commit()
        booking_id = booking.booking_id
        addon_id = addon.addon_id

    client.post("/worker/login", headers=_basic_auth("worker", "secret"))
    resp = client.post(
        f"/worker/jobs/{booking_id}/addons", data={"addon_id": addon_id, "qty": 1}
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_worker_price_adjust_requires_note(client, async_session_maker):
    settings.worker_basic_username = "worker"
    settings.worker_basic_password = "secret"
    settings.worker_team_id = 1
    booking_id = await _seed_booking(async_session_maker, team_id=1)

    client.post("/worker/login", headers=_basic_auth("worker", "secret"))
    client.post(f"/worker/jobs/{booking_id}/start")
    resp = client.post(
        f"/worker/jobs/{booking_id}/finish",
        data={"price_adjust_reason": "EXTRA_SERVICE"},
    )
    assert resp.status_code == 400

