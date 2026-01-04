import asyncio
import base64
import csv
import io
from decimal import Decimal
import uuid
from datetime import date, datetime, timezone

import pytest

from app.domain.bookings.db_models import Booking
from app.domain.bookings.db_models import Team
from app.domain.export_events.db_models import ExportEvent
from app.domain.invoices import statuses as invoice_statuses
from app.domain.invoices.db_models import Invoice, Payment
from app.domain.leads.db_models import Lead
from app.domain.saas.db_models import Organization
from app.domain.workers.db_models import Worker
from app.settings import settings


ORG_HEADER = "X-Test-Org"


def _auth_headers(username: str, password: str, org_id: uuid.UUID) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}", ORG_HEADER: str(org_id)}


def _lead_payload(name: str) -> dict:
    return {
        "name": name,
        "phone": "780-555-0000",
        "email": f"{name.replace(' ', '').lower()}@example.com",
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


@pytest.fixture(autouse=True)
def admin_credentials():
    original_admin_username = settings.admin_basic_username
    original_admin_password = settings.admin_basic_password
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"
    try:
        yield
    finally:
        settings.admin_basic_username = original_admin_username
        settings.admin_basic_password = original_admin_password


async def _seed_finance_records(async_session_maker):
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    async with async_session_maker() as session:
        session.add_all([Organization(org_id=org_a, name="Org A"), Organization(org_id=org_b, name="Org B")])
        lead_a = Lead(org_id=org_a, **_lead_payload("Org A Lead"))
        lead_b = Lead(org_id=org_b, **_lead_payload("Org B Lead"))
        session.add_all([lead_a, lead_b])
        await session.flush()

        team_a = Team(org_id=org_a, name="Team A")
        team_b = Team(org_id=org_b, name="Team B")
        session.add_all([team_a, team_b])
        await session.flush()

        worker_a = Worker(org_id=org_a, team_id=team_a.team_id, name="Worker A", phone="123", hourly_rate_cents=3000)
        worker_b = Worker(org_id=org_b, team_id=team_b.team_id, name="Worker B", phone="456", hourly_rate_cents=3000)
        session.add_all([worker_a, worker_b])
        await session.flush()

        booking_a = Booking(
            org_id=org_a,
            team_id=team_a.team_id,
            lead_id=lead_a.lead_id,
            assigned_worker_id=worker_a.worker_id,
            starts_at=datetime(2024, 1, 5, 12, 0, tzinfo=timezone.utc),
            duration_minutes=120,
            status="DONE",
        )
        booking_b = Booking(
            org_id=org_b,
            team_id=team_b.team_id,
            lead_id=lead_b.lead_id,
            assigned_worker_id=worker_b.worker_id,
            starts_at=datetime(2024, 1, 6, 12, 0, tzinfo=timezone.utc),
            duration_minutes=120,
            status="DONE",
        )
        session.add_all([booking_a, booking_b])
        await session.flush()

        invoice_a1 = Invoice(
            org_id=org_a,
            invoice_number="A-001",
            order_id=booking_a.booking_id,
            customer_id=lead_a.lead_id,
            status=invoice_statuses.INVOICE_STATUS_SENT,
            issue_date=date(2024, 1, 5),
            currency="CAD",
            subtotal_cents=10000,
            taxable_subtotal_cents=10000,
            tax_cents=500,
            tax_rate_basis=Decimal("0.05"),
            total_cents=10500,
        )
        invoice_a2 = Invoice(
            org_id=org_a,
            invoice_number="A-002",
            order_id=booking_a.booking_id,
            customer_id=lead_a.lead_id,
            status=invoice_statuses.INVOICE_STATUS_PAID,
            issue_date=date(2024, 2, 1),
            currency="CAD",
            subtotal_cents=20000,
            taxable_subtotal_cents=0,
            tax_cents=0,
            total_cents=20000,
        )
        invoice_b1 = Invoice(
            org_id=org_b,
            invoice_number="B-001",
            order_id=booking_b.booking_id,
            customer_id=lead_b.lead_id,
            status=invoice_statuses.INVOICE_STATUS_SENT,
            issue_date=date(2024, 1, 10),
            currency="CAD",
            subtotal_cents=15000,
            taxable_subtotal_cents=15000,
            tax_cents=750,
            tax_rate_basis=Decimal("0.05"),
            total_cents=15750,
        )
        session.add_all([invoice_a1, invoice_a2, invoice_b1])
        await session.flush()

        session.add_all(
            [
                Payment(
                    org_id=org_a,
                    invoice_id=invoice_a1.invoice_id,
                    provider="manual",
                    method="cash",
                    amount_cents=10500,
                    currency="CAD",
                    status=invoice_statuses.PAYMENT_STATUS_SUCCEEDED,
                    received_at=datetime(2024, 1, 6, 9, 0, tzinfo=timezone.utc),
                    created_at=datetime(2024, 1, 6, 9, 0, tzinfo=timezone.utc),
                ),
                Payment(
                    org_id=org_a,
                    invoice_id=invoice_a2.invoice_id,
                    provider="manual",
                    method="cash",
                    amount_cents=20000,
                    currency="CAD",
                    status=invoice_statuses.PAYMENT_STATUS_SUCCEEDED,
                    received_at=datetime(2024, 2, 2, 9, 0, tzinfo=timezone.utc),
                    created_at=datetime(2024, 2, 2, 9, 0, tzinfo=timezone.utc),
                ),
                Payment(
                    org_id=org_a,
                    booking_id=booking_a.booking_id,
                    provider="manual",
                    method="cash",
                    amount_cents=5000,
                    currency="CAD",
                    status=invoice_statuses.PAYMENT_STATUS_SUCCEEDED,
                    received_at=datetime(2024, 1, 4, 10, 0, tzinfo=timezone.utc),
                    created_at=datetime(2024, 1, 4, 10, 0, tzinfo=timezone.utc),
                ),
                Payment(
                    org_id=org_b,
                    invoice_id=invoice_b1.invoice_id,
                    provider="manual",
                    method="cash",
                    amount_cents=15750,
                    currency="CAD",
                    status=invoice_statuses.PAYMENT_STATUS_SUCCEEDED,
                    received_at=datetime(2024, 1, 11, 9, 0, tzinfo=timezone.utc),
                    created_at=datetime(2024, 1, 11, 9, 0, tzinfo=timezone.utc),
                ),
                Payment(
                    org_id=org_b,
                    booking_id=booking_b.booking_id,
                    provider="manual",
                    method="cash",
                    amount_cents=4000,
                    currency="CAD",
                    status=invoice_statuses.PAYMENT_STATUS_SUCCEEDED,
                    received_at=datetime(2024, 1, 3, 10, 0, tzinfo=timezone.utc),
                    created_at=datetime(2024, 1, 3, 10, 0, tzinfo=timezone.utc),
                ),
            ]
        )

        await session.commit()
        return {
            "org_a": org_a,
            "org_b": org_b,
            "invoices_a": [invoice_a1, invoice_a2],
            "invoice_b": invoice_b1,
            "booking_a": booking_a,
            "booking_b": booking_b,
        }


def test_admin_exports_are_org_scoped(client, async_session_maker):
    seeded = asyncio.run(_seed_finance_records(async_session_maker))
    headers = _auth_headers("admin", "secret", seeded["org_a"])
    other_org_invoice = seeded["invoice_b"].invoice_number

    gst_resp = client.get(
        "/v1/admin/reports/gst?from=2024-01-01&to=2024-12-31",
        headers=headers,
    )
    assert gst_resp.status_code == 200
    gst_payload = gst_resp.json()
    assert gst_payload["invoice_count"] == len(seeded["invoices_a"])
    assert gst_payload["taxable_subtotal_cents"] == 10000
    assert gst_payload["tax_cents"] == 500

    sales_resp = client.get(
        "/v1/admin/exports/sales-ledger.csv?from=2024-01-01&to=2024-12-31",
        headers=headers,
    )
    assert sales_resp.status_code == 200
    sales_rows = list(csv.DictReader(io.StringIO(sales_resp.text)))
    numbers = [row["invoice_number"] for row in sales_rows]
    assert set(numbers) == {inv.invoice_number for inv in seeded["invoices_a"]}
    assert other_org_invoice not in numbers

    payments_resp = client.get(
        "/v1/admin/exports/payments.csv?from=2024-01-01&to=2024-12-31",
        headers=headers,
    )
    assert payments_resp.status_code == 200
    payment_rows = list(csv.DictReader(io.StringIO(payments_resp.text)))
    exported_numbers = {row["invoice_number"] for row in payment_rows}
    assert exported_numbers == {inv.invoice_number for inv in seeded["invoices_a"]}
    assert other_org_invoice not in exported_numbers

    deposits_resp = client.get(
        "/v1/admin/exports/deposits.csv?from=2024-01-01&to=2024-12-31",
        headers=headers,
    )
    assert deposits_resp.status_code == 200
    deposit_rows = list(csv.DictReader(io.StringIO(deposits_resp.text)))
    assert len(deposit_rows) == 1
    assert [row["booking_id"] for row in deposit_rows] == [seeded["booking_a"].booking_id]

    pnl_resp = client.get(
        "/v1/admin/reports/pnl?from=2024-01-01&to=2024-12-31",
        headers=headers,
    )
    assert pnl_resp.status_code == 200
    pnl_rows = pnl_resp.json()["rows"]
    assert pnl_rows
    assert {row["invoice_number"] for row in pnl_rows} == {inv.invoice_number for inv in seeded["invoices_a"]}
    assert all(row["invoice_number"] != other_org_invoice for row in pnl_rows)


def test_export_dead_letter_scoped(client, async_session_maker):
    org_a, org_b = uuid.uuid4(), uuid.uuid4()

    async def _seed_events():
        async with async_session_maker() as session:
            session.add_all([Organization(org_id=org_a, name="Org A"), Organization(org_id=org_b, name="Org B")])
            session.add_all(
                [
                    ExportEvent(event_id="a1", org_id=org_a, lead_id="lead-a", mode="webhook"),
                    ExportEvent(event_id="b1", org_id=org_b, lead_id="lead-b", mode="webhook"),
                ]
            )
            await session.commit()

    asyncio.run(_seed_events())
    headers = _auth_headers("admin", "secret", org_a)

    resp = client.get("/v1/admin/export-dead-letter", headers=headers)
    assert resp.status_code == 200
    payload = resp.json()
    assert {event["event_id"] for event in payload} == {"a1"}
