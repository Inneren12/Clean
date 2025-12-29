import base64
import datetime
from datetime import timezone

import pytest
import sqlalchemy as sa

from app.domain.admin_audit.db_models import AdminAuditLog
from app.domain.bookings.db_models import Booking
from app.domain.leads.db_models import Lead
from app.settings import settings


def _basic_auth_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


async def _seed_booking(async_session_maker) -> str:
    async with async_session_maker() as session:
        lead = Lead(
            name="Audit Lead",
            phone="780-555-0000",
            email="audit@example.com",
            postal_code="T5A",
            address="123 Test St",
            preferred_dates=["Mon"],
            structured_inputs={"beds": 2, "baths": 1, "cleaning_type": "standard"},
            estimate_snapshot={
                "price_cents": 10000,
                "subtotal_cents": 10000,
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
            team_id=1,
            lead_id=lead.lead_id,
            starts_at=datetime.datetime.now(tz=timezone.utc),
            duration_minutes=60,
            status="PENDING",
        )
        session.add(booking)
        await session.commit()
        await session.refresh(booking)
        return booking.booking_id


@pytest.mark.anyio
async def test_booking_confirm_audited_once(client, async_session_maker):
    settings.dispatcher_basic_username = "dispatch"
    settings.dispatcher_basic_password = "secret"
    headers = _basic_auth_header("dispatch", "secret")

    booking_id = await _seed_booking(async_session_maker)

    resp = client.post(f"/v1/admin/bookings/{booking_id}/confirm", headers=headers)
    assert resp.status_code == 200

    async with async_session_maker() as session:
        result = await session.execute(
            sa.select(AdminAuditLog).where(AdminAuditLog.action == "booking_confirm")
        )
        logs = result.scalars().all()
        assert len(logs) == 1
        log = logs[0]
        assert log.before is not None
        assert log.after is not None
