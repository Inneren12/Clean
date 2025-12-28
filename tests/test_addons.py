import base64
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.addons import schemas as addon_schemas
from app.domain.addons import service as addon_service
from app.domain.bookings.db_models import Booking
from app.domain.leads.db_models import Lead
from app.settings import settings


def _auth_headers(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.mark.anyio
async def test_order_addons_snapshot_and_invoice(client, async_session_maker):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"

    async with async_session_maker() as session:
        addon = await addon_service.create_definition(
            session,
            addon_schemas.AddonDefinitionCreate(
                code="OVEN",
                name="Oven cleaning",
                price_cents=2500,
                default_minutes=30,
            ),
        )
        lead = Lead(
            name="Addon Lead",
            phone="780-111-2222",
            email="lead@example.com",
            postal_code="T5A",
            structured_inputs={"beds": 1, "baths": 1, "cleaning_type": "standard"},
            estimate_snapshot={
                "price_cents": 15000,
                "subtotal_cents": 15000,
                "total_before_tax": 15000,
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
            starts_at=datetime.now(tz=timezone.utc),
            duration_minutes=90,
            planned_minutes=90,
            status="PENDING",
        )
        session.add(booking)
        await session.commit()
        await session.refresh(booking)
        order_id = booking.booking_id
        addon_id = addon.addon_id

    headers = _auth_headers("admin", "secret")

    update_resp = client.patch(
        f"/v1/orders/{order_id}/addons",
        headers=headers,
        json={"addons": [{"addon_id": addon_id, "qty": 2}]},
    )
    assert update_resp.status_code == 200

    async with async_session_maker() as session:
        booking = await session.get(Booking, order_id)
        assert booking.planned_minutes == 150

    client.patch(
        f"/v1/admin/addons/{addon_id}",
        headers=headers,
        json={"price_cents": 4000, "default_minutes": 45},
    )

    list_resp = client.get(f"/v1/orders/{order_id}/addons", headers=headers)
    assert list_resp.status_code == 200
    addon_payload = list_resp.json()[0]
    assert addon_payload["unit_price_cents"] == 2500
    assert addon_payload["minutes"] == 30

    invoice_resp = client.post(
        f"/v1/admin/orders/{order_id}/invoice",
        headers=headers,
        json={"currency": "CAD", "items": [{"description": "Base", "qty": 1, "unit_price_cents": 10000}]},
    )
    assert invoice_resp.status_code == 201
    invoice_data = invoice_resp.json()
    addon_lines = [item for item in invoice_data["items"] if item["description"] == "Oven cleaning"]
    assert addon_lines
    assert addon_lines[0]["qty"] == 2
    assert addon_lines[0]["unit_price_cents"] == 2500


@pytest.mark.anyio
async def test_addon_report_endpoint(client, async_session_maker):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"

    async with async_session_maker() as session:
        addon = await addon_service.create_definition(
            session,
            addon_schemas.AddonDefinitionCreate(
                code="WIN",
                name="Window cleaning",
                price_cents=1200,
                default_minutes=20,
            ),
        )

        now = datetime.now(tz=timezone.utc)
        for qty in (1, 2):
            booking = Booking(
                team_id=1,
                lead_id=None,
                starts_at=now - timedelta(days=qty),
                duration_minutes=60,
                planned_minutes=60,
                status="PENDING",
            )
            session.add(booking)
            await session.flush()
            await addon_service.set_order_addons(
                session,
                booking.booking_id,
                [addon_schemas.OrderAddonSelection(addon_id=addon.addon_id, qty=qty)],
            )
        await session.commit()

    headers = _auth_headers("admin", "secret")
    report_resp = client.get(
        "/v1/admin/reports/addons",
        headers=headers,
        params={"from": (now - timedelta(days=3)).isoformat(), "to": (now + timedelta(days=1)).isoformat()},
    )
    assert report_resp.status_code == 200
    payload = report_resp.json()
    assert payload["addons"]
    entry = payload["addons"][0]
    assert entry["total_qty"] == 3
    assert entry["revenue_cents"] == 3600
