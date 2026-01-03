from datetime import datetime, timedelta, timezone

import pytest

from app.domain.bookings.db_models import Booking
from app.domain.leads.db_models import Lead
from app.main import app
from app.settings import settings


@pytest.mark.anyio
async def test_signed_download_redirects_and_is_no_store(client, async_session_maker):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"

    original_backend = settings.order_storage_backend
    original_backend_state = getattr(app.state, "storage_backend", None)
    original_backend_config = getattr(app.state, "storage_backend_config", None)
    settings.order_storage_backend = "memory"
    app.state.storage_backend = None
    app.state.storage_backend_config = None

    try:
        async with async_session_maker() as session:
            lead = Lead(
                name="Photo Redirect Lead",
                phone="780-555-9999",
                email="redirect@example.com",
                postal_code="T5A",
                address="22 Redirect Ave",
                preferred_dates=["Fri"],
                structured_inputs={"beds": 2, "baths": 1, "cleaning_type": "standard"},
                estimate_snapshot={
                    "price_cents": 3000,
                    "subtotal_cents": 3000,
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
                org_id=settings.default_org_id,
                team_id=1,
                lead_id=lead.lead_id,
                starts_at=datetime.now(tz=timezone.utc) + timedelta(hours=1),
                duration_minutes=60,
                status="CONFIRMED",
                consent_photos=True,
            )
            session.add(booking)
            await session.commit()
            booking_id = booking.booking_id

        upload = client.post(
            f"/v1/orders/{booking_id}/photos",
            data={"phase": "AFTER"},
            files={"file": ("after.jpg", b"photo-bytes", "image/jpeg")},
            auth=("admin", "secret"),
        )
        assert upload.status_code == 201
        photo_id = upload.json()["photo_id"]

        signed = client.get(
            f"/v1/orders/{booking_id}/photos/{photo_id}/signed_url",
            auth=("admin", "secret"),
        )
        assert signed.status_code == 200
        download_url = signed.json()["url"]

        redirect = client.get(download_url, follow_redirects=False)
        assert redirect.status_code == settings.photo_download_redirect_status
        assert redirect.headers["Cache-Control"] == "no-store, private"
        assert redirect.headers["Pragma"] == "no-cache"
        assert redirect.headers["Location"].startswith("https://example.invalid/orders/")
    finally:
        settings.order_storage_backend = original_backend
        app.state.storage_backend = original_backend_state
        app.state.storage_backend_config = original_backend_config
