import uuid

import pytest

from app.domain.saas import service as saas_service
from app.domain.saas.db_models import MembershipRole, Organization
from tests.conftest import DEFAULT_ORG_ID


@pytest.mark.anyio
async def test_duplicate_email_globally_unique(async_session_maker):
    async with async_session_maker() as session:
        org_one = await session.get(Organization, DEFAULT_ORG_ID)
        if not org_one:
            org_one = Organization(org_id=DEFAULT_ORG_ID, name="Default Org")
            session.add(org_one)
            await session.flush()
        user = await saas_service.create_user(session, email="owner@example.com", password="secret")
        await saas_service.create_membership(session, org_one, user, MembershipRole.OWNER)
        await session.commit()

    async with async_session_maker() as session:
        org_two = Organization(org_id=uuid.uuid4(), name="Second Org")
        session.add(org_two)
        await session.flush()
        with pytest.raises(Exception):
            await saas_service.create_user(session, email="owner@example.com", password="secret2")


@pytest.mark.anyio
async def test_tenant_isolation_on_member_listing(async_session_maker, client):
    async with async_session_maker() as session:
        org_one = await saas_service.create_organization(session, "Org A")
        org_two = await saas_service.create_organization(session, "Org B")
        user_a = await saas_service.create_user(session, "a@example.com", "pw")
        await saas_service.create_membership(session, org_one, user_a, MembershipRole.ADMIN)
        await session.commit()

    login_response = client.post(
        "/v1/auth/login",
        json={"email": "a@example.com", "password": "pw", "org_id": str(org_one.org_id)},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    forbidden = client.get(
        f"/v1/auth/orgs/{org_two.org_id}/members",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert forbidden.status_code == 403


@pytest.mark.anyio
async def test_rbac_finance_denied_for_viewer(async_session_maker, client):
    async with async_session_maker() as session:
        org = await saas_service.create_organization(session, "Finance Org")
        viewer = await saas_service.create_user(session, "viewer@example.com", "pw")
        await saas_service.create_membership(session, org, viewer, MembershipRole.VIEWER)
        await session.commit()

    login_response = client.post(
        "/v1/auth/login",
        json={"email": "viewer@example.com", "password": "pw", "org_id": str(org.org_id)},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    resp = client.get(
        "/v1/admin/finance/reports",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code in (401, 403)
