import asyncio
import base64
import uuid
from datetime import date

import pytest

from app.domain.invoices import service as invoice_service
from app.domain.invoices import statuses
from app.domain.invoices.db_models import Invoice
from app.domain.saas.db_models import Organization
from app.settings import settings


def _auth_headers(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture(autouse=True)
def admin_credentials():
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"
    yield


def test_invoices_are_org_scoped(monkeypatch, client, async_session_maker):
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()

    def _resolve_org_id(request):
        header_value = request.headers.get("X-Test-Org")
        return uuid.UUID(header_value) if header_value else settings.default_org_id

    monkeypatch.setattr("app.api.entitlements.resolve_org_id", _resolve_org_id)

    async def _seed() -> tuple[str, str, str]:
        async with async_session_maker() as session:
            session.add_all(
                [
                    Organization(org_id=org_a, name="Org A"),
                    Organization(org_id=org_b, name="Org B"),
                ]
            )
            invoice_a = Invoice(
                org_id=org_a,
                invoice_number=f"A-{uuid.uuid4()}",
                status=statuses.INVOICE_STATUS_DRAFT,
                issue_date=date.today(),
                currency="CAD",
                subtotal_cents=1000,
                tax_cents=0,
                total_cents=1000,
            )
            invoice_b = Invoice(
                org_id=org_b,
                invoice_number=f"B-{uuid.uuid4()}",
                status=statuses.INVOICE_STATUS_DRAFT,
                issue_date=date.today(),
                currency="CAD",
                subtotal_cents=1000,
                tax_cents=0,
                total_cents=1000,
            )
            session.add_all([invoice_a, invoice_b])
            await session.flush()
            token = await invoice_service.upsert_public_token(session, invoice_b)
            await session.commit()
            return invoice_a.invoice_id, invoice_b.invoice_id, token

    invoice_a_id, invoice_b_id, token = asyncio.run(_seed())

    headers = {**_auth_headers("admin", "secret"), "X-Test-Org": str(org_a)}

    list_resp = client.get("/v1/admin/invoices", headers=headers)
    assert list_resp.status_code == 200
    invoice_ids = {inv["invoice_id"] for inv in list_resp.json()["invoices"]}
    assert invoice_a_id in invoice_ids
    assert invoice_b_id not in invoice_ids

    allowed_detail = client.get(f"/v1/admin/invoices/{invoice_a_id}", headers=headers)
    assert allowed_detail.status_code == 200

    detail_resp = client.get(f"/v1/admin/invoices/{invoice_b_id}", headers=headers)
    assert detail_resp.status_code == 404

    checkout_resp = client.post(
        "/v1/payments/invoice/checkout",
        headers=headers,
        params={"invoice_id": invoice_b_id},
    )
    assert checkout_resp.status_code == 404

    token_resp = client.get(f"/i/{token}", headers={"X-Test-Org": str(org_a)})
    assert token_resp.status_code == 404
