import pytest

from app.domain.saas import service as saas_service
from app.domain.saas.db_models import MembershipRole


@pytest.mark.anyio
async def test_admin_reset_temp_password_invalidates_sessions(async_session_maker, client):
    async with async_session_maker() as session:
        org = await saas_service.create_organization(session, "Reset Org")
        admin = await saas_service.create_user(session, "admin@example.com", "SecretAdmin123!")
        admin_membership = await saas_service.create_membership(session, org, admin, MembershipRole.ADMIN)
        user = await saas_service.create_user(session, "worker@example.com", "InitialPass123!")
        await saas_service.create_membership(session, org, user, MembershipRole.WORKER)
        await session.commit()

    admin_token = saas_service.build_access_token(admin, admin_membership)

    login_resp = client.post(
        "/v1/auth/login",
        json={"email": "worker@example.com", "password": "InitialPass123!", "org_id": str(org.org_id)},
    )
    assert login_resp.status_code == 200
    worker_access = login_resp.json()["access_token"]

    reset_resp = client.post(
        f"/v1/admin/users/{user.user_id}/reset-temp-password",
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
