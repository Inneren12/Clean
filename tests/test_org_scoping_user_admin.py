import pytest

from app.domain.saas import service as saas_service
from app.domain.saas.db_models import MembershipRole


@pytest.mark.anyio
async def test_cross_org_admin_actions_forbidden(async_session_maker, client):
    async with async_session_maker() as session:
        org_a = await saas_service.create_organization(session, "Org A")
        org_b = await saas_service.create_organization(session, "Org B")
        admin_a = await saas_service.create_user(session, "admin_a@example.com", "AdminPassword123!")
        membership_a = await saas_service.create_membership(session, org_a, admin_a, MembershipRole.ADMIN)
        user_b = await saas_service.create_user(session, "user_b@example.com", "UserPassword123!")
        await saas_service.create_membership(session, org_b, user_b, MembershipRole.WORKER)
        await session.commit()

    admin_token = saas_service.build_access_token(admin_a, membership_a)

    reset = client.post(
        f"/v1/admin/users/{user_b.user_id}/reset-temp-password",
        json={"reason": "cross"},
        headers={
            "Authorization": f"Bearer {admin_token}",
            "X-Test-Org": str(org_b.org_id),
            "Idempotency-Key": "cross-org-reset",
        },
    )
    assert reset.status_code == 403

    create = client.post(
        "/v1/admin/users",
        json={"email": "new_b@example.com", "target_type": "worker"},
        headers={"Authorization": f"Bearer {admin_token}", "X-Test-Org": str(org_b.org_id)},
    )
    assert create.status_code == 403
