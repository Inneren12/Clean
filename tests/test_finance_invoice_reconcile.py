import asyncio
import base64
import uuid
from datetime import date, datetime, timezone

import pytest
import sqlalchemy as sa

from app.domain.invoices import statuses
from app.domain.invoices.db_models import Invoice, Payment
from app.domain.admin_audit.db_models import AdminAuditLog
from app.domain.saas.db_models import Organization
from app.settings import settings


ORG_HEADER = "X-Test-Org"


@pytest.fixture(autouse=True)
def finance_credentials():
    original_admin_username = settings.admin_basic_username
    original_admin_password = settings.admin_basic_password
    original_dispatcher_username = settings.dispatcher_basic_username
    original_dispatcher_password = settings.dispatcher_basic_password
    original_viewer_username = settings.viewer_basic_username
    original_viewer_password = settings.viewer_basic_password

    settings.admin_basic_username = "finance"
    settings.admin_basic_password = "secret"
    settings.dispatcher_basic_username = "dispatch"
    settings.dispatcher_basic_password = "pw"
    settings.viewer_basic_username = "viewer"
    settings.viewer_basic_password = "pw"
    yield

    settings.admin_basic_username = original_admin_username
    settings.admin_basic_password = original_admin_password
    settings.dispatcher_basic_username = original_dispatcher_username
    settings.dispatcher_basic_password = original_dispatcher_password
    settings.viewer_basic_username = original_viewer_username
    settings.viewer_basic_password = original_viewer_password


def _auth_headers(username: str, password: str, org_id: uuid.UUID) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}", ORG_HEADER: str(org_id)}


async def _seed_invoice_mismatches(async_session_maker):
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    async with async_session_maker() as session:
        session.add_all(
            [
                Organization(org_id=org_a, name="Org A"),
                Organization(org_id=org_b, name="Org B"),
            ]
        )

        pending_with_payment = Invoice(
            org_id=org_a,
            invoice_number="A-001",
            status=statuses.INVOICE_STATUS_SENT,
            issue_date=date(2024, 1, 1),
            currency="CAD",
            subtotal_cents=2000,
            tax_cents=0,
            total_cents=2000,
        )
        paid_without_payment = Invoice(
            org_id=org_a,
            invoice_number="A-002",
            status=statuses.INVOICE_STATUS_PAID,
            issue_date=date(2024, 1, 2),
            currency="CAD",
            subtotal_cents=1500,
            tax_cents=0,
            total_cents=1500,
        )
        duplicate_payment = Invoice(
            org_id=org_a,
            invoice_number="A-003",
            status=statuses.INVOICE_STATUS_PARTIAL,
            issue_date=date(2024, 1, 3),
            currency="CAD",
            subtotal_cents=1000,
            tax_cents=0,
            total_cents=1000,
        )
        clean_invoice = Invoice(
            org_id=org_a,
            invoice_number="A-004",
            status=statuses.INVOICE_STATUS_SENT,
            issue_date=date(2024, 1, 4),
            currency="CAD",
            subtotal_cents=800,
            tax_cents=0,
            total_cents=800,
        )

        org_b_invoice = Invoice(
            org_id=org_b,
            invoice_number="B-001",
            status=statuses.INVOICE_STATUS_SENT,
            issue_date=date(2024, 1, 5),
            currency="CAD",
            subtotal_cents=1200,
            tax_cents=0,
            total_cents=1200,
        )

        session.add_all(
            [
                pending_with_payment,
                paid_without_payment,
                duplicate_payment,
                clean_invoice,
                org_b_invoice,
            ]
        )
        await session.flush()

        session.add_all(
            [
                Payment(
                    org_id=org_a,
                    invoice_id=pending_with_payment.invoice_id,
                    provider="manual",
                    method="cash",
                    amount_cents=500,
                    currency="CAD",
                    status=statuses.PAYMENT_STATUS_SUCCEEDED,
                    received_at=datetime(2024, 1, 2, 9, 0, tzinfo=timezone.utc),
                    created_at=datetime(2024, 1, 2, 9, 0, tzinfo=timezone.utc),
                ),
                Payment(
                    org_id=org_a,
                    invoice_id=duplicate_payment.invoice_id,
                    provider="manual",
                    method="cash",
                    amount_cents=600,
                    currency="CAD",
                    status=statuses.PAYMENT_STATUS_SUCCEEDED,
                    received_at=datetime(2024, 1, 3, 9, 0, tzinfo=timezone.utc),
                    created_at=datetime(2024, 1, 3, 9, 0, tzinfo=timezone.utc),
                ),
                Payment(
                    org_id=org_a,
                    invoice_id=duplicate_payment.invoice_id,
                    provider="manual",
                    method="cash",
                    amount_cents=500,
                    currency="CAD",
                    status=statuses.PAYMENT_STATUS_SUCCEEDED,
                    received_at=datetime(2024, 1, 4, 9, 0, tzinfo=timezone.utc),
                    created_at=datetime(2024, 1, 4, 9, 0, tzinfo=timezone.utc),
                ),
                Payment(
                    org_id=org_b,
                    invoice_id=org_b_invoice.invoice_id,
                    provider="manual",
                    method="cash",
                    amount_cents=1200,
                    currency="CAD",
                    status=statuses.PAYMENT_STATUS_SUCCEEDED,
                    received_at=datetime(2024, 1, 6, 9, 0, tzinfo=timezone.utc),
                    created_at=datetime(2024, 1, 6, 9, 0, tzinfo=timezone.utc),
                ),
            ]
        )

        await session.commit()

    return {
        "org_a": org_a,
        "org_b": org_b,
        "invoices": {
            "pending": pending_with_payment,
            "paid_without_payment": paid_without_payment,
            "duplicate": duplicate_payment,
            "clean": clean_invoice,
            "org_b": org_b_invoice,
        },
    }


async def _seed_reconcile_targets(async_session_maker):
    org_id = uuid.uuid4()
    other_org = uuid.uuid4()
    async with async_session_maker() as session:
        session.add_all(
            [
                Organization(org_id=org_id, name="Org"),
                Organization(org_id=other_org, name="Other"),
            ]
        )

        paid_mismatch = Invoice(
            org_id=org_id,
            invoice_number="R-001",
            status=statuses.INVOICE_STATUS_SENT,
            issue_date=date(2024, 2, 1),
            due_date=date(2024, 2, 10),
            currency="CAD",
            subtotal_cents=1500,
            tax_cents=0,
            total_cents=1500,
        )
        paid_without_funds = Invoice(
            org_id=org_id,
            invoice_number="R-002",
            status=statuses.INVOICE_STATUS_PAID,
            issue_date=date(2024, 3, 1),
            due_date=date(2024, 3, 5),
            currency="CAD",
            subtotal_cents=2000,
            tax_cents=0,
            total_cents=2000,
        )
        partial_invoice = Invoice(
            org_id=org_id,
            invoice_number="R-003",
            status=statuses.INVOICE_STATUS_SENT,
            issue_date=date(2024, 3, 10),
            due_date=date(2024, 3, 20),
            currency="CAD",
            subtotal_cents=1800,
            tax_cents=0,
            total_cents=1800,
        )
        other_invoice = Invoice(
            org_id=other_org,
            invoice_number="R-004",
            status=statuses.INVOICE_STATUS_SENT,
            issue_date=date(2024, 3, 15),
            currency="CAD",
            subtotal_cents=900,
            tax_cents=0,
            total_cents=900,
        )

        session.add_all([paid_mismatch, paid_without_funds, partial_invoice, other_invoice])
        await session.flush()

        session.add_all(
            [
                Payment(
                    org_id=org_id,
                    invoice_id=paid_mismatch.invoice_id,
                    provider="manual",
                    method="cash",
                    amount_cents=1000,
                    currency="CAD",
                    status=statuses.PAYMENT_STATUS_SUCCEEDED,
                    received_at=datetime(2024, 2, 2, 9, 0, tzinfo=timezone.utc),
                    created_at=datetime(2024, 2, 2, 9, 0, tzinfo=timezone.utc),
                ),
                Payment(
                    org_id=org_id,
                    invoice_id=paid_mismatch.invoice_id,
                    provider="manual",
                    method="cash",
                    amount_cents=500,
                    currency="CAD",
                    status=statuses.PAYMENT_STATUS_SUCCEEDED,
                    received_at=datetime(2024, 2, 3, 9, 0, tzinfo=timezone.utc),
                    created_at=datetime(2024, 2, 3, 9, 0, tzinfo=timezone.utc),
                ),
                Payment(
                    org_id=org_id,
                    invoice_id=partial_invoice.invoice_id,
                    provider="manual",
                    method="cash",
                    amount_cents=600,
                    currency="CAD",
                    status=statuses.PAYMENT_STATUS_SUCCEEDED,
                    received_at=datetime(2024, 3, 11, 9, 0, tzinfo=timezone.utc),
                    created_at=datetime(2024, 3, 11, 9, 0, tzinfo=timezone.utc),
                ),
            ]
        )

        await session.commit()

    return {
        "org_id": org_id,
        "other_org": other_org,
        "invoices": {
            "paid_mismatch": paid_mismatch,
            "paid_without_funds": paid_without_funds,
            "partial": partial_invoice,
            "other": other_invoice,
        },
    }


def test_finance_reconcile_requires_finance_role(client, async_session_maker):
    seeded = asyncio.run(_seed_invoice_mismatches(async_session_maker))
    finance_headers = _auth_headers("finance", "secret", seeded["org_a"])

    response = client.get(
        "/v1/admin/finance/reconcile/invoices", headers=finance_headers
    )
    assert response.status_code == 200

    for username in ("dispatch", "viewer"):
        forbidden = client.get(
            "/v1/admin/finance/reconcile/invoices",
            headers=_auth_headers(username, "pw", seeded["org_a"]),
        )
        assert forbidden.status_code == 403


def test_finance_reconcile_post_requires_finance_role(client, async_session_maker):
    seeded = asyncio.run(_seed_reconcile_targets(async_session_maker))
    org_id = seeded["org_id"]
    invoice = seeded["invoices"]["paid_mismatch"]

    for username in ("dispatch", "viewer"):
        forbidden = client.post(
            f"/v1/admin/finance/invoices/{invoice.invoice_id}/reconcile",
            headers=_auth_headers(username, "pw", org_id),
        )
        assert forbidden.status_code == 403


def test_finance_reconcile_lists_org_scoped_mismatches(client, async_session_maker):
    seeded = asyncio.run(_seed_invoice_mismatches(async_session_maker))
    org_a = seeded["org_a"]
    org_b = seeded["org_b"]
    invoices = seeded["invoices"]

    response = client.get(
        "/v1/admin/finance/reconcile/invoices",
        headers=_auth_headers("finance", "secret", org_a),
    )
    assert response.status_code == 200
    payload = response.json()
    numbers = {item["invoice_number"] for item in payload["items"]}
    assert numbers == {"A-001", "A-002", "A-003"}

    pending = next(item for item in payload["items"] if item["invoice_number"] == "A-001")
    assert pending["succeeded_payments_count"] == 1
    assert pending["outstanding_cents"] == 1500
    assert pending["last_payment_at"] is not None
    assert pending["quick_actions"]
    assert pending["quick_actions"][0]["target"] == (
        f"/v1/admin/finance/invoices/{invoices['pending'].invoice_id}/reconcile"
    )

    duplicate = next(item for item in payload["items"] if item["invoice_number"] == "A-003")
    assert duplicate["succeeded_payments_count"] == 2
    assert duplicate["outstanding_cents"] == 0

    paid = next(item for item in payload["items"] if item["invoice_number"] == "A-002")
    assert paid["succeeded_payments_count"] == 0
    assert paid["outstanding_cents"] == invoices["paid_without_payment"].total_cents

    all_response = client.get(
        "/v1/admin/finance/reconcile/invoices?status=all",
        headers=_auth_headers("finance", "secret", org_a),
    )
    assert all_response.status_code == 200
    all_numbers = {item["invoice_number"] for item in all_response.json()["items"]}
    assert "A-004" in all_numbers

    cross_org = client.get(
        "/v1/admin/finance/reconcile/invoices",
        headers=_auth_headers("finance", "secret", org_b),
    )
    assert cross_org.status_code == 200
    cross_numbers = {item["invoice_number"] for item in cross_org.json()["items"]}
    assert cross_numbers == {"B-001"}


def test_finance_reconcile_marks_invoice_paid(client, async_session_maker):
    seeded = asyncio.run(_seed_reconcile_targets(async_session_maker))
    org_id = seeded["org_id"]
    invoice = seeded["invoices"]["paid_mismatch"]
    headers = _auth_headers("finance", "secret", org_id)

    response = client.post(
        f"/v1/admin/finance/invoices/{invoice.invoice_id}/reconcile",
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == statuses.INVOICE_STATUS_PAID
    assert payload["paid_cents"] == invoice.total_cents
    assert payload["outstanding_cents"] == 0
    assert payload["succeeded_payments_count"] == 2

    async def _fetch_state():
        async with async_session_maker() as session:
            refreshed = await session.get(Invoice, invoice.invoice_id)
            audit_logs = await session.scalars(
                sa.select(AdminAuditLog).where(AdminAuditLog.resource_id == invoice.invoice_id)
            )
            return refreshed, list(audit_logs)

    refreshed_invoice, audit_logs = asyncio.run(_fetch_state())
    assert refreshed_invoice.status == statuses.INVOICE_STATUS_PAID
    assert audit_logs
    assert audit_logs[0].action == "finance_reconcile"
    assert audit_logs[0].before["status"] == statuses.INVOICE_STATUS_SENT
    assert audit_logs[0].after["status"] == statuses.INVOICE_STATUS_PAID


def test_finance_reconcile_reopens_unfunded_paid_invoice(client, async_session_maker):
    seeded = asyncio.run(_seed_reconcile_targets(async_session_maker))
    org_id = seeded["org_id"]
    invoice = seeded["invoices"]["paid_without_funds"]
    headers = _auth_headers("finance", "secret", org_id)

    response = client.post(
        f"/v1/admin/finance/invoices/{invoice.invoice_id}/reconcile",
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == statuses.INVOICE_STATUS_OVERDUE
    assert payload["paid_cents"] == 0
    assert payload["outstanding_cents"] == invoice.total_cents

    async def _fetch_invoice():
        async with async_session_maker() as session:
            return await session.get(Invoice, invoice.invoice_id)

    refreshed = asyncio.run(_fetch_invoice())
    assert refreshed.status == statuses.INVOICE_STATUS_OVERDUE


def test_finance_reconcile_is_idempotent(client, async_session_maker):
    seeded = asyncio.run(_seed_reconcile_targets(async_session_maker))
    org_id = seeded["org_id"]
    invoice = seeded["invoices"]["partial"]
    headers = _auth_headers("finance", "secret", org_id)

    first = client.post(
        f"/v1/admin/finance/invoices/{invoice.invoice_id}/reconcile",
        headers=headers,
    )
    assert first.status_code == 200
    payload = first.json()
    assert payload["status"] == statuses.INVOICE_STATUS_PARTIAL
    assert payload["succeeded_payments_count"] == 1

    second = client.post(
        f"/v1/admin/finance/invoices/{invoice.invoice_id}/reconcile",
        headers=headers,
    )
    assert second.status_code == 200
    followup = second.json()
    assert followup == payload

    async def _payment_count():
        async with async_session_maker() as session:
            count = await session.scalar(
                sa.select(sa.func.count(Payment.payment_id)).where(Payment.invoice_id == invoice.invoice_id)
            )
            refreshed = await session.get(Invoice, invoice.invoice_id)
            return int(count or 0), refreshed.status

    payment_count, status_after = asyncio.run(_payment_count())
    assert payment_count == 1
    assert status_after == statuses.INVOICE_STATUS_PARTIAL


def test_finance_reconcile_blocks_cross_org(client, async_session_maker):
    seeded = asyncio.run(_seed_reconcile_targets(async_session_maker))
    org_id = seeded["org_id"]
    other_invoice = seeded["invoices"]["other"]

    response = client.post(
        f"/v1/admin/finance/invoices/{other_invoice.invoice_id}/reconcile",
        headers=_auth_headers("finance", "secret", org_id),
    )

    assert response.status_code == 404
