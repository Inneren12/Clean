import asyncio
import base64
import csv
import io
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.domain.bookings.db_models import Booking, Team
from app.domain.export_events.db_models import ExportEvent
from app.domain.invoices import statuses as invoice_statuses
from app.domain.invoices import service as invoice_service
from app.domain.invoices.db_models import Invoice, Payment
from app.domain.invoices.schemas import InvoiceItemCreate
from app.domain.leads.db_models import Lead
from app.domain.saas.db_models import Organization
from app.jobs import accounting_export
from app.settings import settings


ORG_HEADER = "X-Test-Org"


def _auth_headers(username: str, password: str, org_id: uuid.UUID | None = None) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    headers = {"Authorization": f"Basic {token}"}
    if org_id:
        headers[ORG_HEADER] = str(org_id)
    return headers


def _lead_payload(name: str = "Finance Lead") -> dict:
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


async def _seed_invoices(async_session_maker):
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    async with async_session_maker() as session:
        session.add_all([Organization(org_id=org_a, name="Org A"), Organization(org_id=org_b, name="Org B")])
        lead_a = Lead(org_id=org_a, **_lead_payload("Org A Lead"))
        lead_b = Lead(org_id=org_b, **_lead_payload("Org B Lead"))
        session.add_all([lead_a, lead_b])
        await session.flush()

        invoice_a = Invoice(
            org_id=org_a,
            invoice_number="=BAD-001",
            customer_id=lead_a.lead_id,
            status=invoice_statuses.INVOICE_STATUS_SENT,
            issue_date=date(2024, 1, 5),
            currency="CAD",
            subtotal_cents=10000,
            taxable_subtotal_cents=10000,
            tax_cents=500,
            tax_rate_basis=None,
            total_cents=10500,
        )
        invoice_b = Invoice(
            org_id=org_b,
            invoice_number="B-002",
            customer_id=lead_b.lead_id,
            status=invoice_statuses.INVOICE_STATUS_SENT,
            issue_date=date(2024, 1, 6),
            currency="CAD",
            subtotal_cents=5000,
            taxable_subtotal_cents=0,
            tax_cents=0,
            total_cents=5000,
        )
        session.add_all([invoice_a, invoice_b])
        await session.flush()

        payment = Payment(
            org_id=org_a,
            invoice_id=invoice_a.invoice_id,
            provider="manual",
            method="cash",
            amount_cents=10500,
            currency="CAD",
            status=invoice_statuses.PAYMENT_STATUS_SUCCEEDED,
            received_at=datetime(2024, 1, 6, 9, 0, tzinfo=timezone.utc),
            created_at=datetime(2024, 1, 6, 9, 0, tzinfo=timezone.utc),
        )
        session.add(payment)

        await session.commit()
    return org_a, org_b


def test_accounting_export_org_scope_and_csv_safety(client, async_session_maker):
    org_a, org_b = asyncio.run(_seed_invoices(async_session_maker))
    headers = _auth_headers("admin", "secret", org_a)

    resp = client.get("/v1/admin/exports/accounting.csv?from=2024-01-01&to=2024-12-31", headers=headers)
    assert resp.status_code == 200
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["invoice_number"].startswith("'=")
    assert rows[0]["invoice_number"].endswith("001")
    assert rows[0]["paid_cents"] == "10500"

    other_headers = _auth_headers("admin", "secret", org_b)
    other_resp = client.get("/v1/admin/exports/accounting.csv?from=2024-01-01&to=2024-12-31", headers=other_headers)
    assert other_resp.status_code == 200
    other_rows = list(csv.DictReader(io.StringIO(other_resp.text)))
    assert len(other_rows) == 1
    assert all(row["invoice_number"] != rows[0]["invoice_number"] for row in other_rows)


def test_accounting_export_uses_tax_snapshot(client, async_session_maker):
    async def seed_invoice() -> str:
        async with async_session_maker() as session:
            lead = Lead(org_id=settings.default_org_id, **_lead_payload("Snapshot Lead"))
            session.add(lead)
            team = Team(org_id=settings.default_org_id, name="Team")
            session.add(team)
            await session.flush()

            booking = Booking(
                org_id=settings.default_org_id,
                team_id=team.team_id,
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
                    InvoiceItemCreate(description="Taxed", qty=1, unit_price_cents=10000, tax_rate=Decimal("0.05")),
                    InvoiceItemCreate(description="Untaxed", qty=1, unit_price_cents=5000, tax_rate=None),
                ],
                issue_date=date(2024, 5, 1),
                currency="CAD",
            )
            invoice.status = invoice_statuses.INVOICE_STATUS_SENT
            lead.estimate_snapshot = {"tax_rate": "0.20"}
            await session.commit()
            return invoice.invoice_id

    invoice_id = asyncio.run(seed_invoice())
    headers = _auth_headers("admin", "secret")

    resp = client.get(
        "/v1/admin/exports/accounting.csv?from=2024-05-01&to=2024-05-31",
        headers=headers,
    )
    assert resp.status_code == 200
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    assert rows
    assert rows[0]["taxable_subtotal_cents"] == "10000"
    assert rows[0]["tax_cents"] == "500"


def test_accounting_export_job_stores_artifact(async_session_maker):
    org_id = uuid.uuid4()

    async def seed_invoice() -> None:
        async with async_session_maker() as session:
            session.add(Organization(org_id=org_id, name="Org"))
            invoice = Invoice(
                org_id=org_id,
                invoice_number="JOB-1",
                status=invoice_statuses.INVOICE_STATUS_SENT,
                issue_date=date(2024, 2, 1),
                currency="CAD",
                subtotal_cents=1000,
                taxable_subtotal_cents=1000,
                tax_cents=50,
                total_cents=1050,
            )
            session.add(invoice)
            await session.commit()

    asyncio.run(seed_invoice())

    async def run_job() -> dict:
        async with async_session_maker() as session:
            return await accounting_export.run_accounting_export(
                session,
                org_id=org_id,
                range_start=date(2024, 1, 1),
                range_end=date(2024, 12, 31),
                export_mode="off",
            )

    result = asyncio.run(run_job())
    assert result["stored"] == 1

    async def load_event() -> ExportEvent:
        async with async_session_maker() as session:
            return (
                await session.execute(select(ExportEvent).where(ExportEvent.org_id == org_id))
            ).scalar_one()

    event = asyncio.run(load_event())
    assert event.mode == "accounting_csv"
    assert event.payload["kind"] == "accounting_export_v1"
    assert "csv" in event.payload and "invoice_number" in event.payload["csv"]

