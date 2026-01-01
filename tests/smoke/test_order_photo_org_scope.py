import asyncio
import base64
from datetime import datetime, timezone
import uuid

from app.domain.bookings.db_models import Booking
from app.domain.leads.db_models import Lead
from app.domain.saas.db_models import Organization
from app.settings import settings


def _auth_headers(username: str, password: str, org_id: uuid.UUID) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}", "X-Test-Org": str(org_id)}


def test_order_photo_upload_respects_org_header(client, async_session_maker, tmp_path):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"
    settings.order_storage_backend = "local"
    settings.order_upload_root = str(tmp_path)
    settings.auth_secret_key = "smoke-photo-secret"

    org_id = uuid.uuid4()
    headers = _auth_headers("admin", "secret", org_id)

    async def _seed_booking() -> str:
        async with async_session_maker() as session:
            org = Organization(org_id=org_id, name="Smoke Photo Org")
            lead = Lead(
                org_id=org_id,
                name="Photo Lead",
                phone="780-555-0001",
                email="photo@example.com",
                postal_code="T5B",
                address="2 Test St",
                preferred_dates=["Tue"],
                structured_inputs={},
                estimate_snapshot={"price_cents": 1000, "line_items": []},
                pricing_config_version="v1",
                config_hash="hash",
            )
            booking = Booking(
                org_id=org_id,
                team_id=1,
                lead_id=lead.lead_id,
                starts_at=datetime.now(tz=timezone.utc),
                duration_minutes=60,
                status="CONFIRMED",
                consent_photos=True,
            )
            session.add_all([org, lead, booking])
            await session.commit()
            await session.refresh(booking)
            return booking.booking_id

    booking_id = asyncio.run(_seed_booking())

    files = {"file": ("after.jpg", b"hello-image", "image/jpeg")}
    response = client.post(
        f"/v1/orders/{booking_id}/photos",
        data={"phase": "AFTER"},
        files=files,
        headers=headers,
    )

    assert response.status_code == 201, response.text
