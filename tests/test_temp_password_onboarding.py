import uuid

import pytest

from app.domain.saas import service as saas_service
from app.domain.saas.db_models import MembershipRole


@pytest.mark.anyio
async def test_temp_password_onboarding_flow(async_session_maker, client):
    async with async_session_maker() as session:
        org = await saas_service.create_organization(session, "Temp Org")
        admin = await saas_service.create_user(session, "admin@example.com", "SecretAdmin123!")
        membership = await saas_service.create_membership(session, org, admin, MembershipRole.ADMIN)
        await session.commit()

    token = saas_service.build_access_token(admin, membership)

    create_resp = client.post(
        "/v1/admin/users",
        json={"email": "newuser@example.com", "target_type": "client"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create_resp.status_code == 200
    created = create_resp.json()
    temp_password = created["temp_password"]
    assert created["must_change_password"] is True

    login_resp = client.post(
        "/v1/auth/login",
        json={
            "email": "newuser@example.com",
            "password": temp_password,
            "org_id": str(org.org_id),
        },
    )
    assert login_resp.status_code == 200
    login_payload = login_resp.json()
    assert login_payload["must_change_password"] is True
    access_token = login_payload["access_token"]

    me_resp = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"})
    assert me_resp.status_code == 200
    assert me_resp.json()["must_change_password"] is True

    blocked = client.get(
        "/v1/auth/org-context",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert blocked.status_code == 403

    change_resp = client.post(
        "/v1/auth/change-password",
        json={"current_password": temp_password, "new_password": "BetterPass123!"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert change_resp.status_code == 200
    change_payload = change_resp.json()
    assert change_payload["must_change_password"] is False

    allowed = client.get(
        "/v1/auth/org-context",
        headers={"Authorization": f"Bearer {change_payload['access_token']}"},
    )
    assert allowed.status_code == 200
