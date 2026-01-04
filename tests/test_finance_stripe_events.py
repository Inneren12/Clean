import base64
import uuid
from datetime import datetime, timezone

import pytest

from app.domain.invoices.db_models import StripeEvent
from app.domain.saas.db_models import Organization
from app.settings import settings


def _auth_headers(username: str, password: str, org_id: uuid.UUID) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}", "X-Test-Org": str(org_id)}


@pytest.fixture(autouse=True)
def finance_credentials():
    original_admin_username = settings.admin_basic_username
    original_admin_password = settings.admin_basic_password
    original_viewer_username = settings.viewer_basic_username
    original_viewer_password = settings.viewer_basic_password

    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"
    settings.viewer_basic_username = "viewer"
    settings.viewer_basic_password = "viewer-secret"
    yield

    settings.admin_basic_username = original_admin_username
    settings.admin_basic_password = original_admin_password
    settings.viewer_basic_username = original_viewer_username
    settings.viewer_basic_password = original_viewer_password


@pytest.mark.anyio
async def test_stripe_events_org_scoped(client, async_session_maker):
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()
    now = datetime.now(timezone.utc)

    async with async_session_maker() as session:
        session.add_all([
            Organization(org_id=org_a, name="Org A"),
            Organization(org_id=org_b, name="Org B"),
            StripeEvent(
                event_id="evt_org_a_1",
                status="succeeded",
                payload_hash="hash-a1",
                org_id=org_a,
                event_type="invoice.paid",
                event_created_at=now,
                invoice_id="inv_a",
                booking_id=None,
            ),
            StripeEvent(
                event_id="evt_org_a_2",
                status="error",
                payload_hash="hash-a2",
                org_id=org_a,
                event_type="payment_intent.payment_failed",
                event_created_at=now,
                booking_id="book_a",
                last_error="processing failed",
            ),
            StripeEvent(
                event_id="evt_org_b_1",
                status="succeeded",
                payload_hash="hash-b1",
                org_id=org_b,
                event_type="invoice.paid",
                event_created_at=now,
            ),
        ])
        await session.commit()

    headers = _auth_headers("admin", "secret", org_a)
    resp = client.get("/v1/admin/finance/reconcile/stripe-events", headers=headers)
    assert resp.status_code == 200
    payload = resp.json()
    returned_ids = {item["event_id"] for item in payload["items"]}
    assert returned_ids == {"evt_org_a_1", "evt_org_a_2"}
    assert payload["total"] == 2
    assert payload["limit"] == 50
    assert payload["offset"] == 0


@pytest.mark.anyio
async def test_stripe_events_require_finance_role(client, async_session_maker):
    org_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(Organization(org_id=org_id, name="Org C"))
        session.add(
            StripeEvent(
                event_id="evt_requires_finance",
                status="succeeded",
                payload_hash="hash-c1",
                org_id=org_id,
                event_type="invoice.paid",
                event_created_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    headers = _auth_headers("viewer", "viewer-secret", org_id)
    resp = client.get("/v1/admin/finance/reconcile/stripe-events", headers=headers)
    assert resp.status_code == 403
