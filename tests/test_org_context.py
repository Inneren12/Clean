import base64
import jwt
import pytest

from app.domain.clients import service as client_service
from app.domain.saas import service as saas_service
from app.domain.saas.db_models import MembershipRole
from app.settings import settings


def _basic(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


@pytest.mark.anyio
async def test_saas_token_missing_org_rejected(async_session_maker, client):
    settings.legacy_basic_auth_enabled = False

    async with async_session_maker() as session:
        org = await saas_service.create_organization(session, "Missing Org")
        user = await saas_service.create_user(session, "missing@example.com", "pw")
        membership = await saas_service.create_membership(session, org, user, MembershipRole.ADMIN)
        await session.commit()

    token = jwt.encode({"sub": str(user.user_id), "role": membership.role.value}, settings.auth_secret_key, algorithm="HS256")

    response = client.get(
        "/v1/auth/org-context",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401


@pytest.mark.anyio
async def test_saas_token_malformed_org_rejected(async_session_maker, client):
    settings.legacy_basic_auth_enabled = False

    async with async_session_maker() as session:
        org = await saas_service.create_organization(session, "Bad Org")
        user = await saas_service.create_user(session, "bad@example.com", "pw")
        membership = await saas_service.create_membership(session, org, user, MembershipRole.ADMIN)
        await session.commit()

    token = jwt.encode(
        {"sub": str(user.user_id), "role": membership.role.value, "org_id": "not-a-uuid"},
        settings.auth_secret_key,
        algorithm="HS256",
    )

    response = client.get(
        "/v1/auth/org-context",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401


@pytest.mark.anyio
async def test_saas_token_inactive_membership_rejected(async_session_maker, client):
    settings.legacy_basic_auth_enabled = False

    async with async_session_maker() as session:
        org = await saas_service.create_organization(session, "Inactive Org")
        user = await saas_service.create_user(session, "inactive@example.com", "pw")
        membership = await saas_service.create_membership(session, org, user, MembershipRole.ADMIN)
        membership.is_active = False
        await session.commit()

    token = saas_service.build_access_token(user, membership)

    response = client.get(
        "/v1/auth/org-context",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401


@pytest.mark.anyio
async def test_cross_org_access_blocked(async_session_maker, client):
    settings.legacy_basic_auth_enabled = False

    async with async_session_maker() as session:
        org_a = await saas_service.create_organization(session, "Org A")
        org_b = await saas_service.create_organization(session, "Org B")
        user = await saas_service.create_user(session, "admin@example.com", "pw")
        membership = await saas_service.create_membership(session, org_a, user, MembershipRole.ADMIN)
        await session.commit()

    token = saas_service.build_access_token(user, membership)

    response = client.get(
        f"/v1/auth/orgs/{org_b.org_id}/members",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


@pytest.mark.anyio
async def test_org_context_set_for_identities(async_session_maker, client):
    settings.legacy_basic_auth_enabled = False
    settings.worker_basic_username = "worker"
    settings.worker_basic_password = "secret"
    settings.worker_team_id = 1
    settings.worker_portal_secret = "worker-secret"

    async with async_session_maker() as session:
        org = await saas_service.create_organization(session, "Context Org")
        user = await saas_service.create_user(session, "owner@example.com", "pw")
        membership = await saas_service.create_membership(session, org, user, MembershipRole.OWNER)
        client_user = await client_service.get_or_create_client(session, "client@example.com")
        await session.commit()

    token = saas_service.build_access_token(user, membership)

    saas_response = client.get(
        "/v1/auth/org-context",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert saas_response.status_code == 200
    assert saas_response.json()["org_id"] == str(membership.org_id)

    worker_login = client.post(
        "/worker/login",
        headers={"Authorization": _basic(settings.worker_basic_username, settings.worker_basic_password)},
    )
    assert worker_login.status_code == 200

    worker_response = client.get("/worker/org-context")
    assert worker_response.status_code == 200
    assert worker_response.json()["org_id"] == str(settings.default_org_id)

    magic = client_service.issue_magic_token(
        email=client_user.email,
        client_id=client_user.client_id,
        secret=settings.client_portal_secret,
        ttl_minutes=settings.client_portal_token_ttl_minutes,
    )
    client_response = client.get(
        "/client/org-context",
        headers={"Authorization": f"Bearer {magic}"},
    )

    assert client_response.status_code == 200
    assert client_response.json()["org_id"] == str(settings.default_org_id)
