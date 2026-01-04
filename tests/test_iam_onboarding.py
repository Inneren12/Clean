import pytest

from app.domain.saas import service as saas_service
from app.domain.saas.db_models import MembershipRole


@pytest.mark.anyio
async def test_iam_onboarding_flow(async_session_maker, client):
    async with async_session_maker() as session:
        org = await saas_service.create_organization(session, "IAM Org")
        admin = await saas_service.create_user(session, "admin@example.com", "StrongAdmin123!")
        membership = await saas_service.create_membership(session, org, admin, MembershipRole.ADMIN)
        await session.commit()

    token = saas_service.build_access_token(admin, membership)

    create_resp = client.post(
        "/v1/iam/users",
        json={"email": "worker@example.com", "role": "worker"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create_resp.status_code == 200
    created = create_resp.json()
    temp_password = created["temp_password"]
    assert created["must_change_password"] is True
    assert created["role"] == "worker"

    login_resp = client.post(
        "/v1/auth/login",
        json={
            "email": "worker@example.com",
            "password": temp_password,
            "org_id": str(org.org_id),
        },
    )
    assert login_resp.status_code == 200
    login_payload = login_resp.json()
    assert login_payload["must_change_password"] is True

    blocked = client.get(
        "/v1/auth/org-context",
        headers={"Authorization": f"Bearer {login_payload['access_token']}"},
    )
    assert blocked.status_code == 403

    change_resp = client.post(
        "/v1/auth/change-password",
        json={"current_password": temp_password, "new_password": "BetterPass123!"},
        headers={"Authorization": f"Bearer {login_payload['access_token']}"},
    )
    assert change_resp.status_code == 200
    updated = change_resp.json()
    assert updated["must_change_password"] is False

    allowed = client.get(
        "/v1/auth/org-context",
        headers={"Authorization": f"Bearer {updated['access_token']}"},
    )
    assert allowed.status_code == 200


@pytest.mark.anyio
async def test_iam_reset_revokes_sessions(async_session_maker, client):
    async with async_session_maker() as session:
        org = await saas_service.create_organization(session, "Reset Org")
        admin = await saas_service.create_user(session, "admin@example.com", "SecretAdmin123!")
        admin_membership = await saas_service.create_membership(session, org, admin, MembershipRole.ADMIN)
        worker = await saas_service.create_user(session, "worker@example.com", "InitialPass123!")
        await saas_service.create_membership(session, org, worker, MembershipRole.WORKER)
        await session.commit()

    admin_token = saas_service.build_access_token(admin, admin_membership)

    login_resp = client.post(
        "/v1/auth/login",
        json={"email": "worker@example.com", "password": "InitialPass123!", "org_id": str(org.org_id)},
    )
    assert login_resp.status_code == 200
    worker_access = login_resp.json()["access_token"]

    reset_resp = client.post(
        f"/v1/iam/users/{worker.user_id}/reset-temp-password",
        json={"reason": "support"},
        headers={"Authorization": f"Bearer {admin_token}", "Idempotency-Key": "reset-temp"},
    )
    assert reset_resp.status_code == 200
    new_temp = reset_resp.json()["temp_password"]

    revoked = client.get(
        "/v1/auth/org-context",
        headers={"Authorization": f"Bearer {worker_access}"},
    )
    assert revoked.status_code == 401

    bad_login = client.post(
        "/v1/auth/login",
        json={"email": "worker@example.com", "password": "InitialPass123!", "org_id": str(org.org_id)},
    )
    assert bad_login.status_code == 401

    temp_login = client.post(
        "/v1/auth/login",
        json={"email": "worker@example.com", "password": new_temp, "org_id": str(org.org_id)},
    )
    assert temp_login.status_code == 200
    assert temp_login.json()["must_change_password"] is True


@pytest.mark.anyio
async def test_iam_org_scope_forbidden(async_session_maker, client):
    async with async_session_maker() as session:
        org_a = await saas_service.create_organization(session, "Org A")
        org_b = await saas_service.create_organization(session, "Org B")
        admin_a = await saas_service.create_user(session, "admin_a@example.com", "AdminPassword123!")
        membership_a = await saas_service.create_membership(session, org_a, admin_a, MembershipRole.ADMIN)
        user_b = await saas_service.create_user(session, "user_b@example.com", "UserPassword123!")
        await saas_service.create_membership(session, org_b, user_b, MembershipRole.WORKER)
        await session.commit()

    admin_a_token = saas_service.build_access_token(admin_a, membership_a)

    reset = client.post(
        f"/v1/iam/users/{user_b.user_id}/reset-temp-password",
        json={"reason": "cross"},
        headers={"Authorization": f"Bearer {admin_a_token}", "Idempotency-Key": "cross-reset"},
    )
    assert reset.status_code == 403

    deactivate = client.post(
        f"/v1/iam/users/{user_b.user_id}/deactivate",
        headers={"Authorization": f"Bearer {admin_a_token}"},
    )
    assert deactivate.status_code == 403

    create_cross = client.post(
        "/v1/iam/users",
        json={"email": "another@example.com", "role": "worker"},
        headers={"Authorization": f"Bearer {admin_a_token}", "X-Test-Org": str(org_b.org_id)},
    )
    assert create_cross.status_code == 200
    created = create_cross.json()
    assert created["role"] == "worker"
    assert created["membership_active"] is True
