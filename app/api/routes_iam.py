import logging
import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_auth import AdminIdentity, AdminPermission, AdminRole
from app.api.idempotency import enforce_org_action_rate_limit, require_idempotency
from app.api.saas_auth import ROLE_TO_ADMIN_ROLE, SaaSIdentity, require_permissions
from app.domain.saas import service as saas_service
from app.domain.saas.db_models import Membership, MembershipRole, Organization, PasswordResetEvent, User
from app.domain.admin_audit import service as audit_service
from app.infra.db import get_db_session
from app.infra.email import resolve_app_email_adapter
from app.settings import settings

router = APIRouter(prefix="/v1/iam", tags=["iam"])
logger = logging.getLogger(__name__)


class IAMUserResponse(BaseModel):
    membership_id: int
    user_id: uuid.UUID
    email: EmailStr
    role: MembershipRole
    membership_active: bool
    user_active: bool
    must_change_password: bool


class IAMUserListResponse(BaseModel):
    users: list[IAMUserResponse]


class IAMCreateUserRequest(BaseModel):
    email: EmailStr
    role: MembershipRole = MembershipRole.VIEWER


class IAMCreateUserResponse(IAMUserResponse):
    temp_password: str


class IAMUpdateRoleRequest(BaseModel):
    role: MembershipRole


class IAMResetPasswordRequest(BaseModel):
    reason: str | None = None


class IAMStatusResponse(BaseModel):
    status: str


async def _get_membership_with_user(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[Membership | None, User | None]:
    result = await session.execute(
        sa.select(Membership, User)
        .join(User, User.user_id == Membership.user_id)
        .where(Membership.org_id == org_id, Membership.user_id == user_id)
    )
    row = result.first()
    if not row:
        return None, None
    membership, user = row
    return membership, user


def _serialize_user(membership: Membership, user: User) -> IAMUserResponse:
    return IAMUserResponse(
        membership_id=membership.membership_id,
        user_id=user.user_id,
        email=user.email,
        role=membership.role,
        membership_active=membership.is_active,
        user_active=user.is_active,
        must_change_password=bool(user.must_change_password),
    )


@router.get("/users", response_model=IAMUserListResponse)
async def list_users(
    identity: SaaSIdentity = Depends(require_permissions(AdminPermission.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> IAMUserListResponse:
    rows = await saas_service.list_memberships_for_org(session, identity.org_id)
    return IAMUserListResponse(users=[_serialize_user(membership, user) for membership, user in rows])


@router.post("/users", response_model=IAMCreateUserResponse)
async def create_user(
    payload: IAMCreateUserRequest,
    response: Response,
    identity: SaaSIdentity = Depends(require_permissions(AdminPermission.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> IAMCreateUserResponse:
    org = await session.get(Organization, identity.org_id)
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    normalized_email = saas_service.normalize_email(payload.email)
    user = await session.scalar(sa.select(User).where(User.email == normalized_email))
    membership: Membership | None = None
    if user:
        membership = await session.scalar(
            sa.select(Membership).where(
                Membership.org_id == identity.org_id,
                Membership.user_id == user.user_id,
            )
        )
        if membership and membership.is_active:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already exists")
        if not user.is_active:
            user.is_active = True
            session.add(user)
    else:
        user = await saas_service.create_user(session, normalized_email)

    if membership:
        membership.is_active = True
        membership.role = payload.role
        session.add(membership)
    else:
        membership = await saas_service.create_membership(session, org, user, payload.role)

    temp_password = await saas_service.issue_temp_password(session, user)
    await saas_service.revoke_user_sessions_for_org(
        session, user.user_id, identity.org_id, reason="temp_password_issued"
    )

    response.headers["Cache-Control"] = "no-store"
    await session.commit()
    return IAMCreateUserResponse(
        **_serialize_user(membership, user).model_dump(),
        temp_password=temp_password,
    )


@router.post("/users/{user_id}/reset-temp-password", response_model=IAMCreateUserResponse)
async def reset_temp_password(
    user_id: uuid.UUID,
    payload: IAMResetPasswordRequest,
    request: Request,
    response: Response,
    identity: SaaSIdentity = Depends(require_permissions(AdminPermission.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> IAMCreateUserResponse:
    rate_limited = await enforce_org_action_rate_limit(request, identity.org_id, "reset_temp_password")
    if rate_limited:
        return rate_limited
    idempotency = await require_idempotency(request, session, identity.org_id, "reset_temp_password")
    if isinstance(idempotency, Response):
        return idempotency
    if idempotency.existing_response:
        return idempotency.existing_response
    membership, user = await _get_membership_with_user(session, identity.org_id, user_id)
    if not membership or not user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    temp_password = await saas_service.issue_temp_password(session, user)
    await saas_service.revoke_user_sessions_for_org(
        session, user.user_id, membership.org_id, reason="password_reset"
    )

    event = PasswordResetEvent(
        org_id=membership.org_id,
        user_id=user.user_id,
        actor_admin=identity.email,
        reason=payload.reason,
    )
    session.add(event)

    adapter = resolve_app_email_adapter(request)
    if adapter:
        subject = "Your account password was reset"
        if settings.email_temp_passwords:
            body = (
                "A new temporary password was issued. Log in and change it immediately.\n\n"
                f"Temporary password: {temp_password}\n"
                "This password will only work until you change it."
            )
        else:
            body = "A new temporary password was issued. Please log in and change it immediately."
        try:
            await adapter.send_email(recipient=user.email, subject=subject, body=body)
        except Exception:  # noqa: BLE001
            logger.warning(
                "password_reset_email_failed",
                extra={"extra": {"user_id": str(user.user_id)}},
            )

    response.headers["Cache-Control"] = "no-store"
    response_body = IAMCreateUserResponse(
        **_serialize_user(membership, user).model_dump(),
        temp_password=temp_password,
    )
    audit_identity = AdminIdentity(
        username=identity.email,
        role=ROLE_TO_ADMIN_ROLE.get(identity.role, AdminRole.VIEWER),
        org_id=identity.org_id,
    )
    request.state.explicit_admin_audit = True
    await audit_service.record_action(
        session,
        identity=audit_identity,
        action="reset_temp_password",
        resource_type="user",
        resource_id=str(user.user_id),
        before={"reason": payload.reason},
        after=response_body.model_dump(mode="json"),
    )
    await idempotency.save_response(
        session,
        status_code=status.HTTP_200_OK,
        body=response_body.model_dump(mode="json"),
    )
    await session.commit()
    return response_body


@router.post("/users/{user_id}/deactivate", response_model=IAMUserResponse)
async def deactivate_user(
    user_id: uuid.UUID,
    identity: SaaSIdentity = Depends(require_permissions(AdminPermission.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> IAMUserResponse:
    membership, user = await _get_membership_with_user(session, identity.org_id, user_id)
    if not membership or not user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    await saas_service.deactivate_membership(session, membership, reason="deactivated_by_admin")
    await session.commit()
    return _serialize_user(membership, user)


@router.post("/users/{user_id}/role", response_model=IAMUserResponse)
async def update_role(
    user_id: uuid.UUID,
    payload: IAMUpdateRoleRequest,
    identity: SaaSIdentity = Depends(require_permissions(AdminPermission.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> IAMUserResponse:
    membership, user = await _get_membership_with_user(session, identity.org_id, user_id)
    if not membership or not user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    await saas_service.update_membership_role(session, membership, payload.role)
    await session.commit()
    return _serialize_user(membership, user)


@router.post("/users/{user_id}/logout", response_model=IAMStatusResponse)
async def logout_everywhere(
    user_id: uuid.UUID,
    identity: SaaSIdentity = Depends(require_permissions(AdminPermission.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> IAMStatusResponse:
    membership, user = await _get_membership_with_user(session, identity.org_id, user_id)
    if not membership or not user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    await saas_service.revoke_user_sessions_for_org(
        session, membership.user_id, membership.org_id, reason="admin_logout"
    )
    await session.commit()
    return IAMStatusResponse(status="revoked")

