import asyncio
import uuid
from datetime import date
from typing import Any

import pytest

from app.domain.invoices import statuses as invoice_statuses
from app.domain.invoices.db_models import Invoice
from app.domain.saas.db_models import Organization
from app.infra import stripe_client as stripe_infra
from app.settings import settings


@pytest.fixture(autouse=True)
def _configure_admin_creds():
    original_secret = settings.stripe_secret_key
    settings.stripe_secret_key = "sk_test"
    try:
        yield
    finally:
        settings.stripe_secret_key = original_secret


def test_invoice_checkout_is_org_scoped(client, async_session_maker, monkeypatch):
    org_a, org_b = uuid.uuid4(), uuid.uuid4()

    async def _stub_call(_client: Any, _method: str, **_: Any) -> dict[str, str]:
        return {"url": "https://example.com/checkout", "id": "cs_test"}

    monkeypatch.setattr(stripe_infra, "resolve_client", lambda state: object())
    monkeypatch.setattr(stripe_infra, "call_stripe_client_method", _stub_call)

    async def _seed() -> str:
        async with async_session_maker() as session:
            session.add_all([Organization(org_id=org_a, name="Org A"), Organization(org_id=org_b, name="Org B")])
            invoice = Invoice(
                org_id=org_a,
                invoice_number="INV-ORG-A",
                status=invoice_statuses.INVOICE_STATUS_SENT,
                issue_date=date.today(),
                currency="CAD",
                subtotal_cents=1000,
                tax_cents=0,
                total_cents=1000,
            )
            session.add(invoice)
            await session.commit()
            return invoice.invoice_id

    invoice_id = asyncio.run(_seed())

    headers_a = {"X-Test-Org": str(org_a)}
    success = client.post(f"/v1/payments/invoice/checkout?invoice_id={invoice_id}", headers=headers_a)
    assert success.status_code == 201

    headers_b = {"X-Test-Org": str(org_b)}
    forbidden = client.post(f"/v1/payments/invoice/checkout?invoice_id={invoice_id}", headers=headers_b)
    assert forbidden.status_code == 404
