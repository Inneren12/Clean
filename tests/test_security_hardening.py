import uuid
from datetime import date

import pytest
import sqlalchemy as sa
from starlette.requests import Request

from app.api import entitlements
from app.domain.admin_audit.db_models import AdminAuditLog
from app.domain.invoices import statuses
from app.domain.invoices.db_models import Invoice
from app.settings import settings


def test_x_test_org_header_ignored_in_prod(monkeypatch):
    monkeypatch.undo()
    settings.app_env = "prod"
    settings.testing = False
    random_org = uuid.uuid4()
    request = Request({"type": "http", "headers": [(b"x-test-org", str(random_org).encode())]})

    resolved = entitlements.resolve_org_id(request)

    assert resolved == settings.default_org_id
    assert getattr(request.state, "current_org_id") == settings.default_org_id


@pytest.mark.anyio
async def test_finance_reconcile_audits_snapshots(async_session_maker, client):
    settings.admin_basic_username = "finance"
    settings.admin_basic_password = "secret"
    settings.legacy_basic_auth_enabled = True

    async with async_session_maker() as session:
        invoice = Invoice(
            org_id=settings.default_org_id,
            invoice_number="INV-AUDIT",
            status=statuses.INVOICE_STATUS_PAID,
            issue_date=date.today(),
            due_date=date.today(),
            currency="CAD",
            subtotal_cents=1000,
            taxable_subtotal_cents=0,
            tax_cents=0,
            tax_rate_basis=None,
            total_cents=1000,
            notes=None,
        )
        session.add(invoice)
        await session.commit()

    response = client.post(
        f"/v1/admin/finance/invoices/{invoice.invoice_id}/reconcile",
        auth=("finance", "secret"),
    )

    assert response.status_code == 200

    async with async_session_maker() as session:
        result = await session.execute(
            sa.select(AdminAuditLog)
            .where(AdminAuditLog.action == "finance_reconcile")
            .order_by(AdminAuditLog.created_at.desc())
        )
        audit_log = result.scalars().first()

        assert audit_log is not None
        assert audit_log.org_id == settings.default_org_id
        assert audit_log.before.get("org_id") == str(settings.default_org_id)
        assert audit_log.after.get("org_id") == str(settings.default_org_id)
        assert audit_log.before.get("status") != audit_log.after.get("status")


def test_admin_read_only_blocks_writes_without_break_glass(client):
    settings.admin_basic_username = "owner"
    settings.admin_basic_password = "secret"
    settings.admin_read_only = True
    settings.legacy_basic_auth_enabled = True

    response = client.post("/v1/admin/pricing/reload", auth=("owner", "secret"))

    assert response.status_code == 409
