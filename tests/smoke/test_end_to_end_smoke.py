import asyncio
import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

import pytest
import sqlalchemy as sa

from app.domain.bookings.db_models import Booking
from app.domain.invoices import schemas as invoice_schemas
from app.domain.invoices import service as invoice_service, statuses as invoice_statuses
from app.domain.invoices.db_models import Invoice, Payment
from app.domain.leads.db_models import Lead
from app.domain.saas.db_models import Organization
from app.infra.email import EmailAdapter
from app.main import app
from app.settings import settings

pytestmark = pytest.mark.smoke


def _auth_headers(username: str, password: str, org_id: uuid.UUID) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}", "X-Test-Org": str(org_id)}


class RecordingEmailAdapter(EmailAdapter):
    def __init__(self):
        super().__init__()
        self.sent: list[tuple[str, str, str]] = []

    async def send_email(self, recipient: str, subject: str, body: str, *, headers=None) -> bool:  # type: ignore[override]
        self.sent.append((recipient, subject, body))
        return True


@pytest.fixture()
def smoke_org(async_session_maker):
    org_id = uuid.uuid4()
    async def _create_org():
        async with async_session_maker() as session:
            session.add(Organization(org_id=org_id, name="Smoke Org"))
            await session.commit()

    asyncio.run(_create_org())
    return org_id


def test_booking_invoice_payment_email_and_storage(client, async_session_maker, tmp_path, smoke_org):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"
    settings.order_storage_backend = "local"
    settings.order_upload_root = str(tmp_path)
    settings.auth_secret_key = "smoke-secret"
    headers = _auth_headers("admin", "secret", smoke_org)

    # Create lead + booking tied to the smoke org
    starts_at = datetime.now(tz=timezone.utc) + timedelta(hours=2)

    async def _seed_booking() -> str:
        async with async_session_maker() as session:
            lead = Lead(
                org_id=smoke_org,
                name="Smoke Lead",
                phone="780-555-0000",
                email="smoke@example.com",
                postal_code="T5A",
                address="1 Test St",
                preferred_dates=["Mon"],
                structured_inputs={"beds": 2, "baths": 1, "cleaning_type": "standard"},
                estimate_snapshot={
                    "price_cents": 15000,
                    "subtotal_cents": 15000,
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
                org_id=smoke_org,
                team_id=1,
                lead_id=lead.lead_id,
                starts_at=starts_at,
                duration_minutes=90,
                status="CONFIRMED",
                consent_photos=True,
            )
            session.add(booking)
            await session.commit()
            await session.refresh(booking)
            return booking.booking_id

    booking_id = asyncio.run(_seed_booking())

    # Create invoice from booking and trigger invoice email send
    email_adapter = RecordingEmailAdapter()
    app.state.email_adapter = email_adapter

    async def _create_invoice() -> str:
        async with async_session_maker() as session:
            booking = await session.get(Booking, booking_id)
            settings.invoice_public_token_secret = "smoke-token"
            invoice = await invoice_service.create_invoice_from_order(
                session=session,
                order=booking,
                items=[invoice_schemas.InvoiceItemCreate(description="Service", qty=1, unit_price_cents=15000)],
                currency="CAD",
            )
            invoice.org_id = smoke_org
            token = await invoice_service.upsert_public_token(session, invoice)
            await session.commit()
            return token

    public_token = asyncio.run(_create_invoice())

    send_resp = client.post(f"/v1/admin/invoices/{public_token}/send", headers=headers)
    assert send_resp.status_code == 404, "send should not accept token"

    invoice_list = client.get("/v1/admin/invoices", headers=headers)
    assert invoice_list.status_code == 200
    invoice_id = invoice_list.json()["invoices"][0]["invoice_id"]

    send_resp = client.post(f"/v1/admin/invoices/{invoice_id}/send", headers=headers)
    assert send_resp.status_code == 202
    assert len(email_adapter.sent) == 1

    # Simulate Stripe webhook to mark paid
    settings.stripe_webhook_secret = "whsec_smoke"
    event = {
        "id": "evt_smoke",
        "type": "payment_intent.succeeded",
        "created": int(datetime.now(tz=timezone.utc).timestamp()),
        "data": {"object": {"id": "pi_smoke", "amount_received": 15000, "currency": "CAD", "metadata": {"invoice_id": invoice_id}}},
    }
    app.state.stripe_client = type("StubStripe", (), {"verify_webhook": staticmethod(lambda **_: event)})

    webhook = client.post("/v1/payments/stripe/webhook", content=b"{}", headers={"Stripe-Signature": "t=1"})
    assert webhook.status_code == 200, webhook.text

    async def _invoice_status() -> tuple[str, int]:
        async with async_session_maker() as session:
            invoice = await session.get(Invoice, invoice_id)
            payments = await session.scalar(
                sa.select(sa.func.count()).select_from(Payment).where(Payment.invoice_id == invoice_id)
            )
            return invoice.status if invoice else "", int(payments or 0)

    status_value, payment_count = asyncio.run(_invoice_status())
    assert status_value == invoice_statuses.INVOICE_STATUS_PAID
    assert payment_count == 1

    # Upload and download an order photo
    files = {"file": ("after.jpg", b"hello-image", "image/jpeg")}
    upload = client.post(
        f"/v1/orders/{booking_id}/photos",
        data={"phase": "AFTER"},
        files=files,
        headers=headers,
    )
    assert upload.status_code == 201, upload.text
    photo_id = upload.json()["photo_id"]

    signed = client.get(f"/v1/orders/{booking_id}/photos/{photo_id}/signed_url", headers=headers)
    assert signed.status_code == 200
    signed_url = signed.json()["url"]

    download = client.get(signed_url)
    assert download.status_code == 200
    assert download.content == b"hello-image"

    stored_files = list(Path(tmp_path / "orders" / str(smoke_org) / booking_id).glob("*"))
    assert stored_files, "uploaded file should be persisted"
