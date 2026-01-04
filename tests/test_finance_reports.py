import asyncio
import base64
import csv
import io
from decimal import Decimal
from datetime import date, datetime, timezone

from sqlalchemy import select

from app.domain.bookings.db_models import Booking
from app.domain.invoices import service as invoice_service
from app.domain.invoices import statuses
from app.domain.invoices.schemas import InvoiceItemCreate
from app.domain.invoices.db_models import Invoice, Payment
from app.domain.leads.db_models import Lead
from app.domain.workers.db_models import Worker
from app.settings import settings


def _auth_headers(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _lead_payload(name: str = "Finance Lead") -> dict:
    return {
        "name": name,
        "phone": "780-555-0000",
        "email": "finance@example.com",
        "postal_code": "T5A",
        "address": "1 Finance St",
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


def test_gst_and_exports(client, async_session_maker):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"

    async def seed_data() -> tuple[list[Invoice], Payment, Payment]:
        async with async_session_maker() as session:
            lead = Lead(**_lead_payload())
            session.add(lead)
            await session.flush()

            booking = Booking(
                team_id=1,
                lead_id=lead.lead_id,
                starts_at=datetime(2024, 1, 5, 12, 0, tzinfo=timezone.utc),
                duration_minutes=120,
                status="DONE",
            )
            session.add(booking)
            await session.flush()

            number_one = await invoice_service.generate_invoice_number(session, date(2024, 1, 5))
            invoice_one = Invoice(
                invoice_number=number_one,
                order_id=booking.booking_id,
                customer_id=lead.lead_id,
                status=statuses.INVOICE_STATUS_SENT,
                issue_date=date(2024, 1, 5),
                currency="CAD",
                subtotal_cents=10000,
                taxable_subtotal_cents=10000,
                tax_cents=500,
                tax_rate_basis=Decimal("0.05"),
                total_cents=10500,
            )
            session.add(invoice_one)
            await session.flush()

            payment = Payment(
                invoice_id=invoice_one.invoice_id,
                provider="manual",
                method="cash",
                amount_cents=10500,
                currency="CAD",
                status=statuses.PAYMENT_STATUS_SUCCEEDED,
                received_at=datetime(2024, 1, 6, 9, 0, tzinfo=timezone.utc),
                created_at=datetime(2024, 1, 6, 9, 0, tzinfo=timezone.utc),
            )
            payment.invoice = invoice_one
            session.add(payment)

            deposit_payment = Payment(
                booking_id=booking.booking_id,
                provider="manual",
                method="cash",
                amount_cents=5000,
                currency="CAD",
                status=statuses.PAYMENT_STATUS_SUCCEEDED,
                received_at=datetime(2024, 1, 4, 10, 0, tzinfo=timezone.utc),
                created_at=datetime(2024, 1, 4, 10, 0, tzinfo=timezone.utc),
            )
            session.add(deposit_payment)

            number_two = await invoice_service.generate_invoice_number(session, date(2024, 2, 1))
            invoice_two = Invoice(
                invoice_number=number_two,
                order_id=booking.booking_id,
                customer_id=lead.lead_id,
                status=statuses.INVOICE_STATUS_PAID,
                issue_date=date(2024, 2, 1),
                currency="CAD",
                subtotal_cents=20000,
                taxable_subtotal_cents=0,
                tax_cents=0,
                total_cents=20000,
            )
            session.add(invoice_two)

            session.add(
                Payment(
                    invoice_id=invoice_two.invoice_id,
                    provider="manual",
                    method="cash",
                    amount_cents=20000,
                    currency="CAD",
                    status=statuses.PAYMENT_STATUS_SUCCEEDED,
                    received_at=datetime(2024, 2, 2, 9, 0, tzinfo=timezone.utc),
                    created_at=datetime(2024, 2, 2, 9, 0, tzinfo=timezone.utc),
                    invoice=invoice_two,
                )
            )

            void_number = await invoice_service.generate_invoice_number(session, date(2024, 3, 1))
            void_invoice = Invoice(
                invoice_number=void_number,
                order_id=booking.booking_id,
                customer_id=lead.lead_id,
                status=statuses.INVOICE_STATUS_VOID,
                issue_date=date(2024, 3, 1),
                currency="CAD",
                subtotal_cents=3000,
                taxable_subtotal_cents=3000,
                tax_cents=390,
                tax_rate_basis=Decimal("0.13"),
                total_cents=3390,
            )
            session.add(void_invoice)

            await session.commit()
            return [invoice_one, invoice_two], payment, deposit_payment

    invoices, payment, deposit_payment = asyncio.run(seed_data())
    headers = _auth_headers("admin", "secret")

    gst_resp = client.get(
        "/v1/admin/reports/gst?from=2024-01-01&to=2024-12-31",
        headers=headers,
    )
    assert gst_resp.status_code == 200
    gst_payload = gst_resp.json()
    assert gst_payload["invoice_count"] == 2
    assert gst_payload["taxable_subtotal_cents"] == 10000
    assert gst_payload["tax_cents"] == 500

    sales_resp = client.get(
        "/v1/admin/exports/sales-ledger.csv?from=2024-01-01&to=2024-12-31",
        headers=headers,
    )
    assert sales_resp.status_code == 200
    reader = csv.DictReader(io.StringIO(sales_resp.text))
    rows = list(reader)
    assert [row["invoice_number"] for row in rows] == [inv.invoice_number for inv in invoices]
    assert rows[0]["paid_cents"] == "10500"
    assert rows[1]["balance_due_cents"] == "0"

    payments_resp = client.get(
        "/v1/admin/exports/payments.csv?from=2024-01-01&to=2024-12-31",
        headers=headers,
    )
    assert payments_resp.status_code == 200
    payment_rows = list(csv.DictReader(io.StringIO(payments_resp.text)))
    assert len(payment_rows) == 2
    exported_numbers = {row["invoice_number"] for row in payment_rows}
    assert invoices[0].invoice_number in exported_numbers
    assert invoices[1].invoice_number in exported_numbers

    deposit_resp = client.get(
        "/v1/admin/exports/deposits.csv?from=2024-01-01&to=2024-12-31",
        headers=headers,
    )
    assert deposit_resp.status_code == 200
    deposit_rows = list(csv.DictReader(io.StringIO(deposit_resp.text)))
    assert len(deposit_rows) == 1
    assert deposit_rows[0]["booking_id"] == deposit_payment.booking_id
    assert deposit_rows[0]["amount_cents"] == str(deposit_payment.amount_cents)


def test_gst_report_uses_tax_snapshot(client, async_session_maker):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"

    async def seed_invoice() -> str:
        async with async_session_maker() as session:
            lead = Lead(**_lead_payload("Snapshot Lead"))
            session.add(lead)
            await session.flush()

            booking = Booking(
                team_id=1,
                lead_id=lead.lead_id,
                starts_at=datetime(2024, 5, 1, 12, 0, tzinfo=timezone.utc),
                duration_minutes=90,
                status="DONE",
            )
            session.add(booking)
            await session.flush()

            invoice = await invoice_service.create_invoice_from_order(
                session=session,
                order=booking,
                items=[
                    InvoiceItemCreate(
                        description="Taxed",
                        qty=1,
                        unit_price_cents=10000,
                        tax_rate=Decimal("0.05"),
                    ),
                    InvoiceItemCreate(
                        description="Non-taxed",
                        qty=1,
                        unit_price_cents=5000,
                        tax_rate=None,
                    ),
                ],
                issue_date=date(2024, 5, 1),
                currency="CAD",
            )
            invoice.status = statuses.INVOICE_STATUS_SENT
            lead.estimate_snapshot = {"tax_rate": "0.10"}
            await session.commit()
            return invoice.invoice_id

    invoice_id = asyncio.run(seed_invoice())
    headers = _auth_headers("admin", "secret")

    resp = client.get(
        "/v1/admin/reports/gst?from=2024-05-01&to=2024-05-31",
        headers=headers,
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["invoice_count"] == 1
    assert payload["taxable_subtotal_cents"] == 10000
    assert payload["tax_cents"] == 500

    async def load_invoice() -> Invoice:
        async with async_session_maker() as session:
            invoice = await session.get(Invoice, invoice_id)
            assert invoice is not None
            return invoice

    stored_invoice = asyncio.run(load_invoice())
    assert stored_invoice.taxable_subtotal_cents == 10000
    assert stored_invoice.tax_cents == 500
    assert stored_invoice.tax_rate_basis == Decimal("0.05")


def test_reports_respect_tax_snapshot_after_config_change(client, async_session_maker):
    """GST and P&L should rely on stored invoice snapshots even if tax config changes later."""

    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"

    async def seed_invoice() -> Invoice:
        async with async_session_maker() as session:
            lead = Lead(**_lead_payload("Config Drift Lead"))
            session.add(lead)
            await session.flush()

            worker = Worker(team_id=1, name="Worker", phone="123", hourly_rate_cents=3600)
            session.add(worker)
            await session.flush()

            booking = Booking(
                team_id=1,
                lead_id=lead.lead_id,
                assigned_worker_id=worker.worker_id,
                starts_at=datetime(2024, 6, 1, 15, 0, tzinfo=timezone.utc),
                duration_minutes=90,
                actual_duration_minutes=90,
                status="DONE",
            )
            session.add(booking)
            await session.flush()

            invoice = await invoice_service.create_invoice_from_order(
                session=session,
                order=booking,
                items=[
                    InvoiceItemCreate(
                        description="Taxed",
                        qty=1,
                        unit_price_cents=8000,
                        tax_rate=Decimal("0.05"),
                    ),
                    InvoiceItemCreate(
                        description="Untaxed",
                        qty=1,
                        unit_price_cents=2000,
                        tax_rate=None,
                    ),
                ],
                issue_date=date(2024, 6, 1),
                currency="CAD",
            )
            invoice.status = statuses.INVOICE_STATUS_SENT
            lead.estimate_snapshot = {"tax_rate": "0.13"}  # simulate org tax policy change after issuance
            await session.commit()
            return invoice

    invoice = asyncio.run(seed_invoice())

    headers = _auth_headers("admin", "secret")

    gst_resp = client.get(
        "/v1/admin/reports/gst?from=2024-06-01&to=2024-06-30",
        headers=headers,
    )
    assert gst_resp.status_code == 200
    gst_payload = gst_resp.json()
    assert gst_payload["invoice_count"] == 1
    assert gst_payload["taxable_subtotal_cents"] == 8000
    assert gst_payload["tax_cents"] == 400

    pnl_resp = client.get(
        "/v1/admin/reports/pnl?from=2024-06-01&to=2024-06-30",
        headers=headers,
    )
    assert pnl_resp.status_code == 200
    pnl_payload = pnl_resp.json()
    assert pnl_payload["rows"], pnl_payload
    pnl_row = pnl_payload["rows"][0]
    assert pnl_row["invoice_number"] == invoice.invoice_number
    assert pnl_row["revenue_cents"] == 10000

    async def load_invoice() -> Invoice:
        async with async_session_maker() as session:
            result = await session.execute(select(Invoice).where(Invoice.invoice_id == invoice.invoice_id))
            return result.scalar_one()

    stored_invoice = asyncio.run(load_invoice())
    assert stored_invoice.taxable_subtotal_cents == 8000
    assert stored_invoice.tax_cents == 400
    assert stored_invoice.tax_rate_basis == Decimal("0.05")


def test_pnl_report(client, async_session_maker):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"

    async def seed_booking() -> Invoice:
        async with async_session_maker() as session:
            lead = Lead(**_lead_payload("P&L Lead"))
            session.add(lead)
            await session.flush()

            worker = Worker(team_id=1, name="Worker", phone="123", hourly_rate_cents=3000)
            session.add(worker)
            await session.flush()

            booking = Booking(
                team_id=1,
                lead_id=lead.lead_id,
                assigned_worker_id=worker.worker_id,
                starts_at=datetime(2024, 4, 10, 15, 0, tzinfo=timezone.utc),
                duration_minutes=90,
                actual_duration_minutes=120,
                status="DONE",
            )
            session.add(booking)
            await session.flush()

            number = await invoice_service.generate_invoice_number(session, date(2024, 4, 10))
            invoice = Invoice(
                invoice_number=number,
                order_id=booking.booking_id,
                customer_id=lead.lead_id,
                status=statuses.INVOICE_STATUS_SENT,
                issue_date=date(2024, 4, 10),
                currency="CAD",
                subtotal_cents=12000,
                taxable_subtotal_cents=12000,
                tax_cents=600,
                tax_rate_basis=Decimal("0.05"),
                total_cents=12600,
            )
            session.add(invoice)
            await session.commit()
            return invoice

    invoice = asyncio.run(seed_booking())
    headers = _auth_headers("admin", "secret")

    resp = client.get(
        "/v1/admin/reports/pnl?from=2024-04-01&to=2024-04-30",
        headers=headers,
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["rows"], payload
    row = payload["rows"][0]
    assert row["invoice_number"] == invoice.invoice_number
    assert row["revenue_cents"] == 12000
    assert row["labour_cents"] == 6000
    assert row["margin_cents"] == 6000
    assert abs(row["margin_pct"] - 50.0) < 0.001
