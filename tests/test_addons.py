import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from app.domain.bookings.db_models import Booking
from app.domain.clients import service as client_service
from app.domain.leads.db_models import Lead
from app.settings import settings


def _lead_payload(email: str) -> dict:
    return {
        "name": "Addon Lead",
        "phone": "780-555-9999",
        "email": email,
        "postal_code": "T5A",
        "address": "1 Test St",
        "preferred_dates": ["Mon"],
        "structured_inputs": {"beds": 1, "baths": 1, "cleaning_type": "standard"},
        "estimate_snapshot": {
            "price_cents": 12000,
            "subtotal_cents": 12000,
            "tax_cents": 0,
            "pricing_config_version": "v1",
            "config_hash": "hash",
            "line_items": [],
        },
        "pricing_config_version": "v1",
        "config_hash": "hash",
    }


def _create_order(async_session_maker) -> str:
    async def _build():
        async with async_session_maker() as session:
            email = f"addons-{uuid4()}@example.com"
            client = await client_service.get_or_create_client(session, email, commit=False)
            lead = Lead(**_lead_payload(email))
            session.add(lead)
            await session.flush()

            booking = Booking(
                booking_id=f"order-{uuid4()}",
                client_id=client.client_id,
                lead_id=lead.lead_id,
                team_id=1,
                starts_at=datetime.now(timezone.utc),
                duration_minutes=90,
                planned_minutes=90,
                status="PENDING",
                deposit_required=False,
                deposit_policy=[],
                consent_photos=False,
            )
            session.add(booking)
            await session.commit()
            return booking.booking_id

    return asyncio.run(_build())


def test_admin_can_manage_addon_definitions(client):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"

    create_resp = client.post(
        "/v1/admin/addons",
        auth=(settings.admin_basic_username, settings.admin_basic_password),
        json={
            "code": "PET",
            "name": "Pet Fee",
            "price_cents": 1500,
            "default_minutes": 15,
            "is_active": True,
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    addon_id = create_resp.json()["addon_id"]

    update_resp = client.patch(
        f"/v1/admin/addons/{addon_id}",
        auth=(settings.admin_basic_username, settings.admin_basic_password),
        json={"name": "Pet Cleanup", "price_cents": 2000},
    )
    assert update_resp.status_code == 200
    body = update_resp.json()
    assert body["name"] == "Pet Cleanup"
    assert body["price_cents"] == 2000


def test_order_addons_snapshot_and_invoice_flow(client, async_session_maker):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"

    addon_resp = client.post(
        "/v1/admin/addons",
        auth=(settings.admin_basic_username, settings.admin_basic_password),
        json={
            "code": "OVEN",
            "name": "Oven Cleaning",
            "price_cents": 2500,
            "default_minutes": 30,
            "is_active": True,
        },
    )
    assert addon_resp.status_code == 201
    addon_id = addon_resp.json()["addon_id"]

    order_id = _create_order(async_session_maker)

    patch_payload = {"addons": [{"addon_id": addon_id, "qty": 2}]}
    first_apply = client.patch(
        f"/v1/orders/{order_id}/addons",
        auth=(settings.admin_basic_username, settings.admin_basic_password),
        json=patch_payload,
    )
    assert first_apply.status_code == 200, first_apply.text
    second_apply = client.patch(
        f"/v1/orders/{order_id}/addons",
        auth=(settings.admin_basic_username, settings.admin_basic_password),
        json=patch_payload,
    )
    assert second_apply.status_code == 200, second_apply.text

    list_resp = client.get(
        f"/v1/orders/{order_id}/addons",
        auth=(settings.admin_basic_username, settings.admin_basic_password),
    )
    assert list_resp.status_code == 200
    addons = list_resp.json()
    assert len(addons) == 1
    assert addons[0]["code"] == "OVEN"
    assert addons[0]["unit_price_cents"] == 2500
    assert addons[0]["minutes"] == 30

    invoice_resp = client.post(
        f"/v1/admin/orders/{order_id}/invoice",
        auth=(settings.admin_basic_username, settings.admin_basic_password),
        json={
            "items": [
                {
                    "description": "Base Cleaning",
                    "qty": 1,
                    "unit_price_cents": 12000,
                }
            ]
        },
    )
    assert invoice_resp.status_code == 201, invoice_resp.text
    invoice_body = invoice_resp.json()
    assert len(invoice_body["items"]) == 2
    addon_line = next(item for item in invoice_body["items"] if item["description"] == "Oven Cleaning")
    assert addon_line["qty"] == 2
    assert addon_line["unit_price_cents"] == 2500

    report_resp = client.get(
        "/v1/admin/reports/addons",
        auth=(settings.admin_basic_username, settings.admin_basic_password),
    )
    assert report_resp.status_code == 200
    report_body = report_resp.json()
    assert report_body["addons"], "Expected at least one addon in report"
    assert report_body["addons"][0]["total_qty"] >= 2
    assert report_body["addons"][0]["revenue_cents"] >= 5000
