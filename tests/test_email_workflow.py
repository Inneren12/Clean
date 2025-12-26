import base64
from datetime import datetime, timedelta, timezone
import base64
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from app.domain.bookings.db_models import Booking, EmailEvent
from app.domain.leads.db_models import Lead
from app.infra.email import EmailAdapter
from app.main import app
from app.settings import settings

LOCAL_TZ = ZoneInfo("America/Edmonton")


def _auth_headers(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _make_lead_payload(name: str = "Reminder Lead") -> dict:
    return {
        "name": name,
        "phone": "780-555-1234",
        "email": "customer@example.com",
        "postal_code": "T5A",
        "address": "1 Test St",
        "preferred_dates": ["Mon"],
        "structured_inputs": {"beds": 1, "baths": 1, "cleaning_type": "standard"},
        "estimate_snapshot": {
            "price_cents": 10000,
            "subtotal_cents": 10000,
            "tax_cents": 0,
            "pricing_config_version": "v1",
            "config_hash": "hash",
            "line_items": [],
        },
        "pricing_config_version": "v1",
        "config_hash": "hash",
    }


async def _seed_returning_lead(async_session_maker) -> str:
    async with async_session_maker() as session:
        lead = Lead(**_make_lead_payload(name="Booking Lead"))
        session.add(lead)
        await session.commit()
        await session.refresh(lead)
        history_booking = Booking(
            team_id=1,
            lead_id=lead.lead_id,
            starts_at=datetime.now(tz=timezone.utc) - timedelta(days=7),
            duration_minutes=60,
            status="DONE",
        )
        session.add(history_booking)
        await session.commit()
        return lead.lead_id


def _configure_admin():
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"


def test_email_scan_skips_when_email_disabled(client, async_session_maker):
    _configure_admin()
    settings.email_mode = "off"
    adapter = EmailAdapter()
    original_adapter = getattr(app.state, "email_adapter", None)
    app.state.email_adapter = adapter

    async def _seed() -> None:
        async with async_session_maker() as session:
            lead = Lead(**_make_lead_payload())
            session.add(lead)
            await session.commit()
            await session.refresh(lead)
            booking = Booking(
                team_id=1,
                lead_id=lead.lead_id,
                starts_at=datetime.now(tz=timezone.utc) + timedelta(hours=12),
                duration_minutes=60,
                status="CONFIRMED",
            )
            session.add(booking)
            await session.commit()

    import asyncio

    asyncio.run(_seed())
    headers = _auth_headers("admin", "secret")
    try:
        first = client.post("/v1/admin/email-scan", headers=headers)
        assert first.status_code == 202
        assert first.json()["skipped"] == 1

        async def _count_events() -> int:
            async with async_session_maker() as session:
                result = await session.execute(sa.select(sa.func.count()).select_from(EmailEvent))
                return int(result.scalar_one())

        assert asyncio.run(_count_events()) == 0
    finally:
        app.state.email_adapter = original_adapter


class StubAdapter(EmailAdapter):
    def __init__(self):
        super().__init__()
        self.sent: list[tuple[str, str, str]] = []

    async def send_email(self, recipient: str, subject: str, body: str) -> bool:  # type: ignore[override]
        self.sent.append((recipient, subject, body))
        return True


class FailingAdapter(EmailAdapter):
    async def send_email(self, recipient: str, subject: str, body: str) -> bool:  # type: ignore[override]
        raise RuntimeError("email_failure")


def test_email_scan_is_idempotent(client, async_session_maker):
    _configure_admin()
    adapter = StubAdapter()
    original_adapter = getattr(app.state, "email_adapter", None)
    app.state.email_adapter = adapter

    async def _seed() -> None:
        async with async_session_maker() as session:
            lead = Lead(**_make_lead_payload())
            session.add(lead)
            await session.commit()
            await session.refresh(lead)
            booking = Booking(
                team_id=1,
                lead_id=lead.lead_id,
                starts_at=datetime.now(tz=timezone.utc) + timedelta(hours=12),
                duration_minutes=60,
                status="CONFIRMED",
            )
            session.add(booking)
            await session.commit()

    import asyncio

    asyncio.run(_seed())

    headers = _auth_headers("admin", "secret")
    try:
        first = client.post("/v1/admin/email-scan", headers=headers)
        assert first.status_code == 202
        assert first.json()["sent"] == 1

        second = client.post("/v1/admin/email-scan", headers=headers)
        assert second.status_code == 202
        assert second.json()["sent"] == 0
        assert len(adapter.sent) == 1

        async def _count_events() -> int:
            async with async_session_maker() as session:
                result = await session.execute(sa.select(sa.func.count()).select_from(EmailEvent))
                return int(result.scalar_one())

        assert asyncio.run(_count_events()) == 1
    finally:
        app.state.email_adapter = original_adapter


def test_resend_last_email_replays_latest(client, async_session_maker):
    _configure_admin()
    adapter = StubAdapter()
    original_adapter = getattr(app.state, "email_adapter", None)
    app.state.email_adapter = adapter

    async def _seed() -> str:
        async with async_session_maker() as session:
            lead = Lead(**_make_lead_payload())
            session.add(lead)
            await session.commit()
            await session.refresh(lead)
            booking = Booking(
                team_id=1,
                lead_id=lead.lead_id,
                starts_at=datetime.now(tz=timezone.utc) + timedelta(days=2),
                duration_minutes=90,
                status="CONFIRMED",
            )
            session.add(booking)
            await session.commit()
            await session.refresh(booking)
            event = EmailEvent(
                booking_id=booking.booking_id,
                email_type="booking_pending",
                recipient=lead.email or "customer@example.com",
                subject="Stored subject",
                body="Stored body",
            )
            session.add(event)
            await session.commit()
            return booking.booking_id

    import asyncio

    booking_id = asyncio.run(_seed())
    headers = _auth_headers("admin", "secret")

    try:
        response = client.post(f"/v1/admin/bookings/{booking_id}/resend-last-email", headers=headers)
        assert response.status_code == 202
        assert response.json()["booking_id"] == booking_id
        assert len(adapter.sent) == 1

        async def _count_events() -> int:
            async with async_session_maker() as session:
                result = await session.execute(
                    sa.select(sa.func.count()).select_from(EmailEvent).where(EmailEvent.booking_id == booking_id)
                )
                return int(result.scalar_one())

        assert asyncio.run(_count_events()) == 2
    finally:
        app.state.email_adapter = original_adapter


def test_create_booking_succeeds_when_email_fails(client, async_session_maker):
    original_adapter = getattr(app.state, "email_adapter", None)
    app.state.email_adapter = FailingAdapter()

    import asyncio

    lead_id = asyncio.run(_seed_returning_lead(async_session_maker))

    starts_at_local = datetime.now(tz=LOCAL_TZ).replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=1)
    if starts_at_local.weekday() >= 5:
        starts_at_local += timedelta(days=(7 - starts_at_local.weekday()))
    payload = {"starts_at": starts_at_local.astimezone(timezone.utc).isoformat(), "time_on_site_hours": 1, "lead_id": lead_id}

    try:
        response = client.post("/v1/bookings", json=payload)
        assert response.status_code == 201
    finally:
        app.state.email_adapter = original_adapter
