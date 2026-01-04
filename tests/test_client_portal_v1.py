import asyncio
import logging
from datetime import datetime, timedelta, timezone
import uuid

import pytest

from sqlalchemy import func, select

from app.domain.bookings.db_models import Booking, OrderPhoto, Team
from app.domain.clients.db_models import ClientUser
from app.domain.clients.service import issue_magic_token
from app.domain.invoices import statuses as invoice_statuses
from app.domain.invoices.db_models import Invoice
from app.domain.saas.db_models import Organization
from app.infra.org_context import set_current_org_id
from app.main import app
from app.settings import settings


class _StubStorage:
    def __init__(self) -> None:
        self.called_with: list[str] = []

    async def put(self, *, key: str, body, content_type: str):  # pragma: no cover - unused
        raise NotImplementedError

    async def read(self, *, key: str):  # pragma: no cover - unused
        raise NotImplementedError

    async def delete(self, *, key: str):  # pragma: no cover - unused
        raise NotImplementedError

    async def list(self, *, prefix: str = ""):  # pragma: no cover - unused
        return []

    async def generate_signed_get_url(
        self, *, key: str, expires_in: int, resource_url: str | None = None, variant: str | None = None
    ) -> str:
        self.called_with.append(key)
        return f"https://cdn.example.com/{key}?exp={expires_in}"

    def supports_direct_io(self) -> bool:
        return False


async def _relax_rate_limit() -> None:
    limiter = getattr(app.state, "rate_limiter", None)
    if not limiter:
        return
    limiter.requests_per_minute = max(getattr(limiter, "requests_per_minute", 0) or 0, 1000)
    reset = getattr(limiter, "reset", None)
    if reset:
        if asyncio.iscoroutinefunction(reset):
            await reset()
        else:
            reset()


async def _seed_org_with_booking(session_factory, org_id: uuid.UUID, email: str, booking_id: str, invoice_id: str):
    async with session_factory() as session:
        set_current_org_id(org_id)
        session.add(Organization(org_id=org_id, name=f"Org-{org_id.hex[:6]}"))
        team = Team(org_id=org_id, name=f"Team-{org_id.hex[:4]}")
        session.add(team)

        local_email = email
        if "@" in email:
            user_part, domain_part = email.split("@", 1)
            local_email = f"{user_part}+{org_id.hex[:6]}@{domain_part}"

        result = await session.execute(
            select(ClientUser).where(func.lower(ClientUser.email) == local_email.lower())
        )
        client_user = result.scalar_one_or_none()
        if not client_user:
            client_user = ClientUser(email=local_email)
            session.add(client_user)
            await session.flush()

        booking = Booking(
            booking_id=booking_id,
            org_id=org_id,
            client_id=client_user.client_id,
            team_id=team.team_id,
            starts_at=datetime.now(timezone.utc) + timedelta(days=2),
            duration_minutes=90,
            planned_minutes=90,
            status="CONFIRMED",
            deposit_required=False,
            deposit_policy=[],
            consent_photos=True,
        )
        session.add(booking)

        invoice = Invoice(
            invoice_id=invoice_id,
            org_id=org_id,
            invoice_number=f"INV-{invoice_id}",
            order_id=booking.booking_id,
            status=invoice_statuses.INVOICE_STATUS_SENT,
            issue_date=datetime.now(timezone.utc).date(),
            due_date=None,
            currency="USD",
            subtotal_cents=10000,
            taxable_subtotal_cents=0,
            tax_cents=0,
            total_cents=10000,
        )
        session.add(invoice)

        photo = OrderPhoto(
            photo_id=str(uuid.uuid4()),
            org_id=org_id,
            order_id=booking.booking_id,
            filename="before.jpg",
            original_filename="before.jpg",
            content_type="image/jpeg",
            size_bytes=1234,
            storage_key=f"orders/{org_id}/{booking.booking_id}/before.jpg",
            phase="before",
            uploaded_by="worker",
            sha256="0" * 64,
        )
        session.add(photo)

        await session.commit()
        return client_user, booking, invoice, photo


def _make_token(client_user: ClientUser, org_id: uuid.UUID) -> str:
    return issue_magic_token(
        email=client_user.email,
        client_id=client_user.client_id,
        secret=settings.client_portal_secret,
        ttl_minutes=settings.client_portal_token_ttl_minutes,
        org_id=org_id,
    )


@pytest.mark.anyio
async def test_client_portal_org_scoped_access(client, async_session_maker):
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()
    booking_id_a = "booking-org-a"
    booking_id_b = "booking-org-b"
    invoice_id_a = "invoice-org-a"
    invoice_id_b = "invoice-org-b"

    client_user_a, booking_a, invoice_a, _ = await _seed_org_with_booking(
        async_session_maker, org_a, "client-a@example.com", booking_id_a, invoice_id_a
    )
    _, booking_b, invoice_b, _ = await _seed_org_with_booking(
        async_session_maker, org_b, "client-a@example.com", booking_id_b, invoice_id_b
    )

    token_a = _make_token(client_user_a, org_a)
    client.cookies.set("client_session", token_a)
    await _relax_rate_limit()

    bookings_resp = client.get("/v1/client/portal/bookings")
    assert bookings_resp.status_code == 200
    order_ids = {item["order_id"] for item in bookings_resp.json()}
    assert order_ids == {booking_a.booking_id}

    invoices_resp = client.get("/v1/client/portal/invoices")
    assert invoices_resp.status_code == 200
    invoice_ids = {item["invoice_id"] for item in invoices_resp.json()}
    assert invoice_ids == {invoice_a.invoice_id}

    cross_org_detail = client.get(f"/v1/client/portal/bookings/{booking_b.booking_id}")
    assert cross_org_detail.status_code == 404

    cross_org_invoice = client.get(f"/v1/client/portal/invoices/{invoice_b.invoice_id}")
    assert cross_org_invoice.status_code == 404


@pytest.mark.anyio
async def test_client_photo_flow_redirect_only(client, async_session_maker, monkeypatch):
    org_id = uuid.uuid4()
    booking_id = "booking-photo"
    invoice_id = "invoice-photo"
    client_user, booking, _, photo = await _seed_org_with_booking(
        async_session_maker, org_id, "photo@example.com", booking_id, invoice_id
    )
    token = _make_token(client_user, org_id)
    client.cookies.set("client_session", token)

    stub_storage = _StubStorage()
    monkeypatch.setattr(app.state, "storage_backend", stub_storage)
    await _relax_rate_limit()

    signed_resp = client.get(
        f"/v1/client/portal/bookings/{booking.booking_id}/photos/{photo.photo_id}/signed-url"
    )
    assert signed_resp.status_code == 200
    url = signed_resp.json()["url"]
    assert "/signed-download" in url

    redirect_resp = client.get(url, allow_redirects=False)
    assert redirect_resp.status_code == settings.photo_download_redirect_status
    assert "cdn.example.com" in redirect_resp.headers.get("location", "")


@pytest.mark.anyio
async def test_client_portal_logging_redacts_token(client, async_session_maker, caplog):
    org_id = uuid.uuid4()
    booking_id = "booking-logs"
    invoice_id = "invoice-logs"
    client_user, _, _, _ = await _seed_org_with_booking(
        async_session_maker, org_id, "logs@example.com", booking_id, invoice_id
    )
    token = _make_token(client_user, org_id)
    client.cookies.set("client_session", token)
    await _relax_rate_limit()

    caplog.set_level(logging.INFO)
    response = client.get("/client/orders")
    assert response.status_code == 200
    assert token not in caplog.text
    assert client_user.email not in caplog.text

