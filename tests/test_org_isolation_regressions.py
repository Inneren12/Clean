import base64
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import base64
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.domain.bookings.db_models import Booking
from app.domain.invoices import schemas as invoice_schemas
from app.domain.invoices import service as invoice_service
from app.domain.leads.db_models import Lead
from app.domain.saas.db_models import Organization
from app.settings import settings


def _auth_headers(username: str, password: str, org_id: uuid.UUID) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}", "X-Test-Org": str(org_id)}


@pytest.fixture()
def admin_credentials():
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"
    yield


@pytest.fixture()
def upload_root(tmp_path):
    original_root = settings.order_upload_root
    original_backend = settings.order_storage_backend
    from app.main import app

    original_backend_state = getattr(app.state, "storage_backend", None)
    settings.order_storage_backend = "local"
    settings.order_upload_root = str(tmp_path)
    app.state.storage_backend = None
    yield tmp_path
    settings.order_upload_root = original_root
    settings.order_storage_backend = original_backend
    app.state.storage_backend = original_backend_state


async def _seed_orgs(async_session_maker) -> tuple[uuid.UUID, uuid.UUID]:
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    async with async_session_maker() as session:
        session.add_all([Organization(org_id=org_a, name="Org A"), Organization(org_id=org_b, name="Org B")])
        await session.commit()
    return org_a, org_b


@pytest.mark.anyio
async def test_cross_org_invoice_and_booking_actions_blocked(client, async_session_maker, admin_credentials):
    org_a, org_b = await _seed_orgs(async_session_maker)
    original_default_org = settings.default_org_id
    settings.default_org_id = org_a

    async with async_session_maker() as session:
        booking_b = Booking(
            org_id=org_b,
            team_id=1,
            starts_at=datetime.now(tz=timezone.utc) + timedelta(days=1),
            duration_minutes=60,
            status="PENDING",
        )
        lead = Lead(
            org_id=org_b,
            name="Invoice Lead",
            phone="780-555-1111",
            email="cross@example.com",
            postal_code="T5A",
            address="123 Test St",
            preferred_dates=["Tue"],
            structured_inputs={"beds": 2, "baths": 1, "cleaning_type": "standard"},
            estimate_snapshot={
                "price_cents": 5000,
                "subtotal_cents": 5000,
                "tax_cents": 0,
                "pricing_config_version": "v1",
                "config_hash": "hash",
                "line_items": [],
            },
            pricing_config_version="v1",
            config_hash="hash",
        )
        session.add_all([lead, booking_b])
        await session.flush()
        invoice_b = await invoice_service.create_invoice_from_order(
            session=session,
            order=booking_b,
            items=[invoice_schemas.InvoiceItemCreate(description="Service", qty=1, unit_price_cents=5000)],
            currency="CAD",
        )
        invoice_b.org_id = org_b
        await session.commit()
        invoice_id = invoice_b.invoice_id
        booking_id = booking_b.booking_id

    headers = _auth_headers("admin", "secret", org_a)

    booking_cancel = client.post(f"/v1/admin/bookings/{booking_id}/cancel", headers=headers)
    assert booking_cancel.status_code == 404

    detail = client.get(f"/v1/admin/invoices/{invoice_id}", headers=headers)
    assert detail.status_code == 404

    payment_attempt = client.post(
        f"/v1/admin/invoices/{invoice_id}/record-payment",
        headers=headers,
        json={"amount_cents": 5000, "method": "cash"},
    )
    assert payment_attempt.status_code == 404

    settings.default_org_id = original_default_org


@pytest.mark.anyio
async def test_signed_photo_urls_are_org_scoped(
    client, async_session_maker, admin_credentials, upload_root
):
    org_a, org_b = await _seed_orgs(async_session_maker)
    original_default_org = settings.default_org_id
    settings.default_org_id = org_a

    async with async_session_maker() as session:
        lead = Lead(
            org_id=org_a,
            name="Photo Lead",
            phone="780-555-2222",
            email="photo@example.com",
            postal_code="T5A",
            address="9 Camera St",
            preferred_dates=["Wed"],
            structured_inputs={"beds": 1, "baths": 1, "cleaning_type": "standard"},
            estimate_snapshot={
                "price_cents": 4000,
                "subtotal_cents": 4000,
                "tax_cents": 0,
                "pricing_config_version": "v1",
                "config_hash": "hash",
                "line_items": [],
            },
            pricing_config_version="v1",
            config_hash="hash",
        )
        booking = Booking(
            org_id=org_a,
            team_id=1,
            lead_id=lead.lead_id,
            starts_at=datetime.now(tz=timezone.utc) + timedelta(hours=4),
            duration_minutes=90,
            status="CONFIRMED",
            consent_photos=True,
        )
        session.add_all([lead, booking])
        await session.commit()
        booking_id = booking.booking_id

    files = {"file": ("after.jpg", b"hello-image", "image/jpeg")}
    upload = client.post(
        f"/v1/orders/{booking_id}/photos",
        data={"phase": "AFTER"},
        files=files,
        headers=_auth_headers("admin", "secret", org_a),
    )
    assert upload.status_code == 201
    photo_id = upload.json()["photo_id"]

    settings.default_org_id = org_b
    cross_org_headers = _auth_headers("admin", "secret", org_b)
    signed_cross_org = client.get(
        f"/v1/orders/{booking_id}/photos/{photo_id}/signed_url",
        headers=cross_org_headers,
    )
    assert signed_cross_org.status_code == 404

    settings.default_org_id = org_a
    signed = client.get(
        f"/v1/orders/{booking_id}/photos/{photo_id}/signed_url",
        headers=_auth_headers("admin", "secret", org_a),
    )
    assert signed.status_code == 200
    signed_url = signed.json()["url"]

    cross_org_download = client.get(
        f"/v1/orders/{booking_id}/photos/{photo_id}/signed-download",
        headers=cross_org_headers,
    )
    assert cross_org_download.status_code == 401

    fetch = client.get(signed_url)
    assert fetch.status_code == 200

    stored_files = list(Path(upload_root / "orders" / str(org_a) / booking_id).glob("*"))
    assert stored_files, "file should be stored under the org namespace"

    settings.default_org_id = original_default_org
