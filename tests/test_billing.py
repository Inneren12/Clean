import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import datetime as dt

import pytest
import sqlalchemy as sa

from app.domain.bookings import db_models as booking_db_models
from app.domain.saas import billing_service, plans, service as saas_service
from app.domain.saas.db_models import OrganizationBilling, OrganizationUsageEvent
from app.domain.workers.db_models import Worker
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
async def test_legacy_booking_skips_entitlements(async_session_maker, client):
    starts_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    response = client.post("/v1/bookings", json={"starts_at": starts_at, "time_on_site_hours": 2})
    assert response.status_code != 402

    async with async_session_maker() as session:
        usage_count = await session.scalar(
            sa.select(sa.func.count()).select_from(OrganizationUsageEvent)
        )
        assert usage_count == 0


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


@pytest.mark.anyio
async def test_checkout_session_subscription_defaults_to_incomplete(async_session_maker, client):
    settings.stripe_webhook_secret = "whsec_test"
    event = {
        "id": "evt_sub_checkout",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_sub_123",
                "mode": "subscription",
                "subscription": "sub_checkout",
                "customer": "cus_checkout",
                "metadata": {"org_id": str(DEFAULT_ORG_ID), "plan_id": "pro"},
            }
        },
    }
    app.state.stripe_client = SimpleNamespace(verify_webhook=lambda payload, signature: event)

    resp = client.post("/v1/payments/stripe/webhook", content=b"{}", headers={"Stripe-Signature": "t=test"})
    assert resp.status_code == 200
    assert resp.json()["processed"] is True

    async with async_session_maker() as session:
        stmt = sa.select(OrganizationBilling).where(OrganizationBilling.org_id == DEFAULT_ORG_ID)
        billing = (await session.execute(stmt)).scalar_one()
        assert billing.plan_id == "pro"
        assert billing.status == "incomplete"
        assert billing.stripe_subscription_id == "sub_checkout"


@pytest.mark.anyio
async def test_worker_usage_snapshot_supports_deactivation(async_session_maker):
    async with async_session_maker() as session:
        await billing_service.record_usage_event(
            session, DEFAULT_ORG_ID, metric="worker_created", quantity=1, resource_id="worker-1"
        )
        await billing_service.record_usage_event(
            session, DEFAULT_ORG_ID, metric="worker_created", quantity=-1, resource_id="worker-1"
        )
        await session.commit()

        usage = await billing_service.usage_snapshot(session, DEFAULT_ORG_ID)
        assert usage["workers"] == 0


@pytest.mark.anyio
async def test_usage_report_scoped_and_with_drift(async_session_maker, client):
    settings.auth_secret_key = "secret"
    async with async_session_maker() as session:
        org_one = await saas_service.create_organization(session, "Usage Org A")
        org_two = await saas_service.create_organization(session, "Usage Org B")

        owner = await saas_service.create_user(session, "owner@example.com", "pw")
        finance = await saas_service.create_user(session, "finance@example.com", "pw")

        await saas_service.create_membership(
            session, org_one, owner, saas_service.MembershipRole.OWNER
        )
        await saas_service.create_membership(
            session, org_two, finance, saas_service.MembershipRole.FINANCE
        )

        await billing_service.set_plan(session, org_one.org_id, plan_id="pro", status="active")
        await billing_service.set_plan(session, org_two.org_id, plan_id="free", status="active")

        team_one = booking_db_models.Team(org_id=org_one.org_id, name=f"team-{org_one.org_id}")
        team_two = booking_db_models.Team(org_id=org_two.org_id, name=f"team-{org_two.org_id}")
        session.add_all([team_one, team_two])
        await session.flush()

        worker_one = Worker(
            org_id=org_one.org_id,
            team_id=team_one.team_id,
            name="Worker One",
            phone="111",
            email="worker1@example.com",
            role="tech",
        )
        worker_two = Worker(
            org_id=org_two.org_id,
            team_id=team_two.team_id,
            name="Worker Two",
            phone="222",
            email="worker2@example.com",
            role="tech",
        )
        session.add_all([worker_one, worker_two])
        await session.flush()

        booking_one = booking_db_models.Booking(
            org_id=org_one.org_id,
            team_id=team_one.team_id,
            starts_at=dt.datetime.now(tz=dt.timezone.utc),
            duration_minutes=60,
            status="pending",
        )
        booking_two = booking_db_models.Booking(
            org_id=org_one.org_id,
            team_id=team_one.team_id,
            starts_at=dt.datetime.now(tz=dt.timezone.utc),
            duration_minutes=45,
            status="pending",
        )
        booking_three = booking_db_models.Booking(
            org_id=org_two.org_id,
            team_id=team_two.team_id,
            starts_at=dt.datetime.now(tz=dt.timezone.utc),
            duration_minutes=30,
            status="pending",
        )
        session.add_all([booking_one, booking_two, booking_three])
        await session.flush()

        photo_one = booking_db_models.OrderPhoto(
            order_id=booking_one.booking_id,
            org_id=org_one.org_id,
            phase="before",
            filename="one.jpg",
            original_filename="one.jpg",
            content_type="image/jpeg",
            size_bytes=128,
            sha256="a" * 64,
            uploaded_by="owner",
            storage_provider="local",
            storage_key="orders/one",
        )
        photo_two = booking_db_models.OrderPhoto(
            order_id=booking_three.booking_id,
            org_id=org_two.org_id,
            phase="before",
            filename="two.jpg",
            original_filename="two.jpg",
            content_type="image/jpeg",
            size_bytes=64,
            sha256="b" * 64,
            uploaded_by="finance",
            storage_provider="local",
            storage_key="orders/two",
        )
        session.add_all([photo_one, photo_two])

        await billing_service.record_usage_event(
            session, org_one.org_id, metric="worker_created", quantity=2, resource_id="seed"
        )
        await billing_service.record_usage_event(
            session, org_one.org_id, metric="booking_created", quantity=1, resource_id="seed"
        )
        await session.commit()

    login_one = client.post(
        "/v1/auth/login",
        json={"email": "owner@example.com", "password": "pw", "org_id": str(org_one.org_id)},
    )
    assert login_one.status_code == 200
    token_one = login_one.json()["access_token"]

    login_two = client.post(
        "/v1/auth/login",
        json={"email": "finance@example.com", "password": "pw", "org_id": str(org_two.org_id)},
    )
    assert login_two.status_code == 200
    token_two = login_two.json()["access_token"]

    report_one = client.get(
        "/v1/billing/usage/report",
        headers={"Authorization": f"Bearer {token_one}"},
    )
    assert report_one.status_code == 200
    org_one_data = report_one.json()
    assert org_one_data["org_id"] == str(org_one.org_id)
    assert org_one_data["usage"]["workers"]["value"] == 1
    assert org_one_data["usage"]["bookings_this_month"]["value"] == 2
    assert org_one_data["usage"]["storage_bytes"]["value"] == 128
    assert org_one_data["usage"]["workers"]["limit"] == plans.get_plan("pro").limits.max_workers
    assert org_one_data["drift_detected"] is True
    assert org_one_data["usage"]["workers"]["recorded"] == 2
    assert org_one_data["usage"]["workers"]["drift"] == 1

    report_two = client.get(
        "/v1/billing/usage/report",
        headers={"Authorization": f"Bearer {token_two}"},
    )
    assert report_two.status_code == 200
    org_two_data = report_two.json()
    assert org_two_data["org_id"] == str(org_two.org_id)
    assert org_two_data["usage"]["workers"]["value"] == 1
    assert org_two_data["usage"]["bookings_this_month"]["value"] == 1
    assert org_two_data["usage"]["storage_bytes"]["value"] == 64
    assert org_two_data["usage"]["workers"]["limit"] == plans.get_plan("free").limits.max_workers
    assert org_two_data["drift_detected"] is True


@pytest.mark.anyio
async def test_usage_report_rbac_denies_viewer(async_session_maker, client):
    settings.auth_secret_key = "secret"
    async with async_session_maker() as session:
        org = await saas_service.create_organization(session, "Usage Org C")
        viewer = await saas_service.create_user(session, "viewer@example.com", "pw")
        await saas_service.create_membership(
            session, org, viewer, saas_service.MembershipRole.VIEWER
        )
        await billing_service.set_plan(session, org.org_id, plan_id="free", status="active")
        await session.commit()

    login = client.post(
        "/v1/auth/login",
        json={"email": "viewer@example.com", "password": "pw", "org_id": str(org.org_id)},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    resp = client.get(
        "/v1/billing/usage/report",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
