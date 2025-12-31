import time
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.domain.saas import billing_service, plans, service as saas_service
from app.domain.saas.db_models import OrganizationBilling
from app.main import app
from app.settings import settings
from tests.conftest import DEFAULT_ORG_ID


@pytest.mark.anyio
async def test_subscription_webhook_idempotent(async_session_maker, client):
    settings.stripe_webhook_secret = "whsec_test"
    now_ts = int(time.time())
    event = {
        "id": "evt_sub_update",
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "id": "sub_123",
                "customer": "cus_123",
                "status": "active",
                "metadata": {"org_id": str(DEFAULT_ORG_ID), "plan_id": "pro"},
                "current_period_end": now_ts + 3600,
            }
        },
    }
    app.state.stripe_client = SimpleNamespace(verify_webhook=lambda payload, signature: event)

    resp = client.post("/v1/payments/stripe/webhook", content=b"{}", headers={"Stripe-Signature": "t=test"})
    assert resp.status_code == 200
    assert resp.json()["processed"] is True

    duplicate = client.post("/v1/payments/stripe/webhook", content=b"{}", headers={"Stripe-Signature": "t=test"})
    assert duplicate.status_code == 200
    assert duplicate.json()["processed"] is False

    async with async_session_maker() as session:
        stmt = sa.select(OrganizationBilling).where(OrganizationBilling.org_id == DEFAULT_ORG_ID)
        billing = (await session.execute(stmt)).scalar_one()
        assert billing.plan_id == "pro"
        assert billing.stripe_subscription_id == "sub_123"
        assert billing.status == "active"


@pytest.mark.anyio
async def test_free_plan_booking_limit_enforced(async_session_maker, client):
    settings.stripe_secret_key = None
    async with async_session_maker() as session:
        org = await saas_service.create_organization(session, "Plan Org")
        user = await saas_service.create_user(session, "limit@example.com", "pw")
        await saas_service.create_membership(session, org, user, saas_service.MembershipRole.OWNER)
        await billing_service.set_plan(session, org.org_id, plan_id="free", status="active")
        limit = plans.get_plan("free").limits.max_bookings_per_month
        for i in range(limit):
            await billing_service.record_usage_event(session, org.org_id, metric="booking_created", resource_id=f"seed-{i}")
        await session.commit()

    login = client.post(
        "/v1/auth/login",
        json={"email": "limit@example.com", "password": "pw", "org_id": str(org.org_id)},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    response = client.post(
        "/v1/bookings",
        json={"starts_at": "2030-01-01T10:00:00Z", "time_on_site_hours": 2},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 402
    assert "limit" in response.json()["detail"].lower()
