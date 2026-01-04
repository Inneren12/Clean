import asyncio
import base64
from datetime import date, datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.bookings.db_models import Booking
from app.domain.invoices import service as invoice_service
from app.domain.invoices import statuses
from app.domain.invoices.schemas import InvoiceItemCreate
from app.domain.invoices.db_models import Invoice, InvoicePublicToken, Payment
from app.domain.leads.db_models import Lead
from app.infra.email import EmailAdapter
from app.settings import settings
from app.infra.db import Base


def _auth_headers(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _lead_payload(name: str = "Invoice Lead") -> dict:
    return {
        "name": name,
        "phone": "780-555-1234",
        "email": "lead@example.com",
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


@pytest.mark.anyio
async def test_invoice_numbering_is_unique(tmp_path):
    db_path = tmp_path / "numbers.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def create_invoice() -> str:
        async with Session() as session:
            number = await invoice_service.generate_invoice_number(session, date(2025, 1, 1))
            invoice = Invoice(
                invoice_number=number,
                order_id=None,
                customer_id=None,
                status=statuses.INVOICE_STATUS_DRAFT,
                issue_date=date(2025, 1, 1),
                currency="CAD",
                subtotal_cents=0,
                tax_cents=0,
                total_cents=0,
            )
            session.add(invoice)
            await session.commit()
            return number

    numbers = await asyncio.gather(*(create_invoice() for _ in range(10)))
    assert len(numbers) == len(set(numbers))
    assert all(number.startswith("INV-2025-") for number in numbers)

    await engine.dispose()


@pytest.mark.anyio
async def test_manual_payments_update_status(async_session_maker):
    async with async_session_maker() as session:
        number = await invoice_service.generate_invoice_number(session, date(2025, 1, 1))
        invoice = Invoice(
            invoice_number=number,
            order_id=None,
            customer_id=None,
            status=statuses.INVOICE_STATUS_DRAFT,
            issue_date=date(2025, 1, 1),
            currency="CAD",
            subtotal_cents=1000,
            tax_cents=0,
            total_cents=1000,
        )
        session.add(invoice)
        await session.commit()
        await session.refresh(invoice)

        await invoice_service.record_manual_payment(session, invoice, 500, method="cash")
        await session.commit()
        await session.refresh(invoice)
        assert invoice.status == statuses.INVOICE_STATUS_PARTIAL

        await invoice_service.record_manual_payment(session, invoice, 500, method="cash")
        await session.commit()
        await session.refresh(invoice)
        assert invoice.status == statuses.INVOICE_STATUS_PAID

        payment_count = await session.scalar(
            sa.select(sa.func.count()).select_from(Payment).where(Payment.invoice_id == invoice.invoice_id)
        )
        assert payment_count == 2


@pytest.mark.anyio
async def test_invoice_creation_populates_base_charge_cents(async_session_maker):
    """Test that creating an invoice sets base_charge_cents on the booking"""
    async with async_session_maker() as session:
        # Create a booking with base_charge_cents=0
        booking = Booking(
            team_id=1,
            starts_at=datetime.now(tz=timezone.utc),
            duration_minutes=120,
            status="CONFIRMED",
            base_charge_cents=0,  # Initially 0
        )
        session.add(booking)
        await session.flush()

        # Create invoice from booking
        items = [
            InvoiceItemCreate(
                description="Cleaning Service",
                qty=1,
                unit_price_cents=18000,
                tax_rate=0.13,
            )
        ]
        invoice = await invoice_service.create_invoice_from_order(
            session=session,
            order=booking,
            items=items,
            issue_date=date.today(),
        )
        await session.flush()
        await session.refresh(booking)

        # Verify base_charge_cents is now set to invoice subtotal
        assert booking.base_charge_cents == 18000
        assert invoice.subtotal_cents == 18000

        # Verify that creating a second invoice doesn't overwrite base_charge_cents
        items2 = [
            InvoiceItemCreate(
                description="Additional Service",
                qty=1,
                unit_price_cents=5000,
            )
        ]
        invoice2 = await invoice_service.create_invoice_from_order(
            session=session,
            order=booking,
            items=items2,
            issue_date=date.today(),
        )
        await session.flush()
        await session.refresh(booking)

        # base_charge_cents should remain the same (from first invoice)
        assert booking.base_charge_cents == 18000


def test_admin_invoice_flow(client, async_session_maker):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"

    async def seed_order() -> str:
        async with async_session_maker() as session:
            lead = Lead(**_lead_payload())
            session.add(lead)
            await session.flush()
            booking = Booking(
                team_id=1,
                lead_id=lead.lead_id,
                starts_at=datetime.now(tz=timezone.utc),
                duration_minutes=90,
                status="PENDING",
            )
            session.add(booking)
            await session.commit()
            return booking.booking_id

    order_id = asyncio.run(seed_order())
    headers = _auth_headers("admin", "secret")

    reason_resp = client.post(
        f"/v1/orders/{order_id}/reasons",
        headers=headers,
        json={"kind": "PRICE_ADJUST", "code": "EXTRA_SERVICE", "note": "Custom pricing"},
    )
    assert reason_resp.status_code == 201

    create_resp = client.post(
        f"/v1/admin/orders/{order_id}/invoice",
        headers=headers,
        json={
            "currency": "CAD",
            "items": [
                {"description": "Service", "qty": 1, "unit_price_cents": 15000},
                {"description": "Supplies", "qty": 1, "unit_price_cents": 5000, "tax_rate": 0.05},
            ],
            "notes": "Test invoice",
        },
    )
    assert create_resp.status_code == 201
    invoice_data = create_resp.json()
    assert invoice_data["created_by"] == "admin"
    assert invoice_data["subtotal_cents"] == 20000
    assert invoice_data["tax_cents"] == 250
    assert invoice_data["total_cents"] == 20250
    invoice_id = invoice_data["invoice_id"]
    invoice_number = invoice_data["invoice_number"]

    list_resp = client.get("/v1/admin/invoices", headers=headers, params={"status": "DRAFT"})
    assert list_resp.status_code == 200
    payload = list_resp.json()
    assert payload["total"] >= 1
    assert any(item["invoice_id"] == invoice_id for item in payload["invoices"])

    alias_amount = invoice_data["total_cents"] // 2
    alias_resp = client.post(
        f"/v1/admin/invoices/{invoice_id}/record-payment",
        headers={**headers, "Idempotency-Key": "alias-payment"},
        json={"amount_cents": alias_amount, "method": "cash", "reference": "receipt-1"},
    )
    assert alias_resp.status_code == 201
    alias_payload = alias_resp.json()
    assert alias_payload["invoice"]["status"] == statuses.INVOICE_STATUS_PARTIAL
    assert alias_payload["payment"]["amount_cents"] == alias_amount

    mark_resp = client.post(
        f"/v1/admin/invoices/{invoice_id}/mark-paid",
        headers=headers,
        json={
            "amount_cents": invoice_data["total_cents"] - alias_amount,
            "method": "cash",
            "reference": "receipt-2",
        },
    )
    assert mark_resp.status_code == 201
    paid_payload = mark_resp.json()
    assert paid_payload["invoice"]["status"] == statuses.INVOICE_STATUS_PAID
    assert paid_payload["invoice"]["paid_cents"] == invoice_data["total_cents"]
    assert paid_payload["payment"]["amount_cents"] == invoice_data["total_cents"] - alias_amount

    detail_resp = client.get(f"/v1/admin/invoices/{invoice_id}", headers=headers)
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["status"] == statuses.INVOICE_STATUS_PAID
    assert detail["balance_due_cents"] == 0
    assert len(detail["payments"]) == 2
    assert detail["created_by"] == "admin"

    filtered = client.get(
        "/v1/admin/invoices",
        headers=headers,
        params={"status": "PAID", "q": invoice_number},
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1

    bad_status = client.get(
        "/v1/admin/invoices",
        headers=headers,
        params={"status": "invalid"},
    )
    assert bad_status.status_code == 422


class StubEmailAdapter(EmailAdapter):
    def __init__(self):
        super().__init__()
        self.sent: list[tuple[str, str, str]] = []

    async def send_email(self, recipient: str, subject: str, body: str) -> bool:  # type: ignore[override]
        self.sent.append((recipient, subject, body))
        return True


async def _seed_invoice_with_token(async_session_maker) -> tuple[str, str]:
    async with async_session_maker() as session:
        lead = Lead(**_lead_payload())
        session.add(lead)
        await session.commit()
        await session.refresh(lead)
        booking = Booking(
            team_id=1,
            lead_id=lead.lead_id,
            starts_at=datetime.now(tz=timezone.utc),
            duration_minutes=90,
            status="PENDING",
        )
        session.add(booking)
        await session.flush()
        invoice = await invoice_service.create_invoice_from_order(
            session=session,
            order=booking,
            items=[InvoiceItemCreate(description="Service", qty=1, unit_price_cents=10000)],
            currency="CAD",
        )
        token = await invoice_service.upsert_public_token(session, invoice)
        await session.commit()
        return invoice.invoice_id, token


def test_public_invoice_view_and_pdf(client, async_session_maker):
    previous_secret = settings.invoice_public_token_secret
    settings.invoice_public_token_secret = "test-secret"
    try:
        invoice_id, token = asyncio.run(_seed_invoice_with_token(async_session_maker))

        ok = client.get(f"/i/{token}")
        assert ok.status_code == 200
        assert "Invoice #" in ok.text
        assert "Download PDF" in ok.text

        pdf = client.get(f"/i/{token}.pdf")
        assert pdf.status_code == 200
        assert pdf.headers["content-type"].startswith("application/pdf")
        assert len(pdf.content) > 100

        missing = client.get("/i/not-a-token")
        assert missing.status_code == 404

        async def _count_tokens() -> int:
            async with async_session_maker() as session:
                result = await session.execute(sa.select(InvoicePublicToken))
                rows = result.scalars().all()
                assert all(len(row.token_hash) == 64 for row in rows)
                assert all(row.token_hash != token for row in rows)
                return len(rows)

        assert asyncio.run(_count_tokens()) == 1
    finally:
        settings.invoice_public_token_secret = previous_secret


def test_admin_send_invoice_sets_status_and_token(client, async_session_maker):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"
    previous_public_base_url = settings.public_base_url
    previous_token_secret = settings.invoice_public_token_secret
    settings.public_base_url = "https://example.com"
    settings.invoice_public_token_secret = "test-secret"
    adapter = StubEmailAdapter()
    from app.main import app

    original_adapter = getattr(app.state, "email_adapter", None)
    app.state.email_adapter = adapter

    async def _seed_invoice() -> str:
        async with async_session_maker() as session:
            lead = Lead(**_lead_payload())
            session.add(lead)
            await session.commit()
            await session.refresh(lead)
            booking = Booking(
                team_id=1,
                lead_id=lead.lead_id,
                starts_at=datetime.now(tz=timezone.utc),
                duration_minutes=90,
                status="PENDING",
            )
            session.add(booking)
            await session.flush()
            invoice = await invoice_service.create_invoice_from_order(
                session=session,
                order=booking,
                items=[
                    InvoiceItemCreate(description="Service", qty=1, unit_price_cents=15000),
                    InvoiceItemCreate(description="Supplies", qty=2, unit_price_cents=2500),
                ],
                currency="CAD",
            )
            await session.commit()
            return invoice.invoice_id

    invoice_id = asyncio.run(_seed_invoice())
    headers = _auth_headers("admin", "secret")

    try:
        resp = client.post(f"/v1/admin/invoices/{invoice_id}/send", headers=headers)
        assert resp.status_code == 202
        payload = resp.json()
        assert payload["invoice"]["status"] == statuses.INVOICE_STATUS_SENT
        assert payload["email_sent"] is True
        assert payload["public_link"].startswith("https://example.com/i/")
        token = payload["public_link"].rsplit("/", 1)[-1]
        assert adapter.sent
        assert payload["public_link"] in adapter.sent[0][2]
        assert f"/i/{token}.pdf" in adapter.sent[0][2]

        async def _reload() -> tuple[str, int, str | None]:
            async with async_session_maker() as session:
                invoice = await session.get(Invoice, invoice_id)
                token_row = await session.scalar(sa.select(InvoicePublicToken))
                token_hash = getattr(token_row, "token_hash", None)
                return invoice.status if invoice else "", 1 if token_row else 0, token_hash

        status_value, token_count, token_hash = asyncio.run(_reload())
        assert status_value == statuses.INVOICE_STATUS_SENT
        assert token_count == 1
        assert token_hash and token_hash != token
    finally:
        app.state.email_adapter = original_adapter
        settings.public_base_url = previous_public_base_url
        settings.invoice_public_token_secret = previous_token_secret
