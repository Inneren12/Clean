import uuid

import pytest
import uuid

import pytest
import sqlalchemy as sa

from app.domain.saas import service as saas_service
from app.domain.saas.db_models import MembershipRole, Organization, User
from app.settings import settings


@pytest.mark.anyio
async def test_enroll_and_verify_enables_totp(async_session_maker, client):
    settings.admin_mfa_required = True
    async with async_session_maker() as session:
        org = Organization(org_id=uuid.uuid4(), name="MFA Org")
        user = await saas_service.create_user(session, "mfa@example.com", "SecretPass123!")
        await saas_service.create_membership(session, org, user, MembershipRole.OWNER)
        await session.commit()

    login = client.post(
        "/v1/auth/login",
        json={"email": "mfa@example.com", "password": "SecretPass123!", "org_id": str(org.org_id)},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    enroll = client.post("/v1/auth/2fa/enroll", headers={"Authorization": f"Bearer {token}"})
    assert enroll.status_code == 200
    payload = enroll.json()
    secret = payload["secret"]
    code = saas_service.generate_totp_code(secret)

    verify = client.post(
        "/v1/auth/2fa/verify",
        json={"code": code},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert verify.status_code == 200

    async with async_session_maker() as session:
        db_user = await session.scalar(sa.select(User).where(User.email == "mfa@example.com"))
        assert db_user
        assert db_user.totp_enabled is True
        assert db_user.totp_secret_base32 == secret


@pytest.mark.anyio
async def test_admin_login_requires_mfa_code_and_allows_admin_routes(async_session_maker, client):
    settings.admin_mfa_required = True
    async with async_session_maker() as session:
        org = await saas_service.create_organization(session, "Admin MFA Org")
        user = await saas_service.create_user(session, "admin-mfa@example.com", "SecretPass123!")
        await saas_service.create_membership(session, org, user, MembershipRole.ADMIN)
        await session.commit()

    login = client.post(
        "/v1/auth/login",
        json={"email": "admin-mfa@example.com", "password": "SecretPass123!", "org_id": str(org.org_id)},
    )
    token = login.json()["access_token"]
    enroll = client.post("/v1/auth/2fa/enroll", headers={"Authorization": f"Bearer {token}"})
    secret = enroll.json()["secret"]
    code = saas_service.generate_totp_code(secret)
    verify = client.post(
        "/v1/auth/2fa/verify",
        json={"code": code},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert verify.status_code == 200

    missing_code = client.post(
        "/v1/auth/login",
        json={"email": "admin-mfa@example.com", "password": "SecretPass123!", "org_id": str(org.org_id)},
    )
    assert missing_code.status_code == 401
    assert missing_code.json()["type"] == "mfa_required"

    correct_code = client.post(
        "/v1/auth/login",
        json={
            "email": "admin-mfa@example.com",
            "password": "SecretPass123!",
            "org_id": str(org.org_id),
            "mfa_code": saas_service.generate_totp_code(secret),
        },
    )
    assert correct_code.status_code == 200
    access_token = correct_code.json()["access_token"]
    assert correct_code.json()["mfa_verified"] is True

    iam_resp = client.get("/v1/iam/users", headers={"Authorization": f"Bearer {access_token}"})
    assert iam_resp.status_code == 200


@pytest.mark.anyio
async def test_wrong_mfa_code_rejected(async_session_maker, client):
    settings.admin_mfa_required = True
    async with async_session_maker() as session:
        org = await saas_service.create_organization(session, "Wrong MFA Org")
        user = await saas_service.create_user(session, "wrong-mfa@example.com", "SecretPass123!")
        await saas_service.create_membership(session, org, user, MembershipRole.OWNER)
        await session.commit()

    token = client.post(
        "/v1/auth/login",
        json={"email": "wrong-mfa@example.com", "password": "SecretPass123!", "org_id": str(org.org_id)},
    ).json()["access_token"]

    enroll = client.post("/v1/auth/2fa/enroll", headers={"Authorization": f"Bearer {token}"})
    secret = enroll.json()["secret"]
    client.post(
        "/v1/auth/2fa/verify",
        json={"code": saas_service.generate_totp_code(secret)},
        headers={"Authorization": f"Bearer {token}"},
    )

    wrong_login = client.post(
        "/v1/auth/login",
        json={
            "email": "wrong-mfa@example.com",
            "password": "SecretPass123!",
            "org_id": str(org.org_id),
            "mfa_code": "123456",
        },
    )
    assert wrong_login.status_code == 401
    assert wrong_login.json()["type"] == "mfa_required"


@pytest.mark.anyio
async def test_non_admin_roles_not_blocked(async_session_maker, client):
    settings.admin_mfa_required = True
    async with async_session_maker() as session:
        org = await saas_service.create_organization(session, "Viewer Org")
        user = await saas_service.create_user(session, "viewer@example.com", "SecretPass123!")
        membership = await saas_service.create_membership(session, org, user, MembershipRole.VIEWER)
        user.totp_secret_base32 = saas_service.generate_totp_secret()
        user.totp_enabled = True
        session.add_all([user, membership])
        await session.commit()

    login = client.post(
        "/v1/auth/login",
        json={"email": "viewer@example.com", "password": "SecretPass123!", "org_id": str(org.org_id)},
    )
    assert login.status_code == 200
    assert login.json()["mfa_verified"] is False
