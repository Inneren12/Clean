import asyncio
from datetime import date, datetime, timezone
import base64

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.bookings.db_models import Booking
from app.domain.invoices import service as invoice_service
from app.domain.invoices import statuses
from app.domain.invoices.db_models import Invoice, Payment
from app.domain.leads.db_models import Lead
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

    mark_resp = client.post(
        f"/v1/admin/invoices/{invoice_id}/mark-paid",
        headers=headers,
        json={"amount_cents": invoice_data["total_cents"], "method": "cash", "reference": "receipt-1"},
    )
    assert mark_resp.status_code == 201
    paid_payload = mark_resp.json()
    assert paid_payload["invoice"]["status"] == statuses.INVOICE_STATUS_PAID
    assert paid_payload["invoice"]["paid_cents"] == invoice_data["total_cents"]
    assert paid_payload["payment"]["amount_cents"] == invoice_data["total_cents"]

    detail_resp = client.get(f"/v1/admin/invoices/{invoice_id}", headers=headers)
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["status"] == statuses.INVOICE_STATUS_PAID
    assert detail["balance_due_cents"] == 0
    assert len(detail["payments"]) == 1

    filtered = client.get(
        "/v1/admin/invoices",
        headers=headers,
        params={"status": "PAID", "q": invoice_number},
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
