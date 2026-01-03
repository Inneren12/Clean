import uuid
from datetime import datetime

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_auth import AdminPermission
from app.api.org_context import require_org_context
from app.api.saas_auth import require_permissions, require_saas_user
from app.domain.saas import service as saas_service
from app.domain.saas.db_models import Membership, MembershipRole, User
from app.infra.db import get_db_session
from app.settings import settings

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str
    org_id: uuid.UUID | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    org_id: uuid.UUID
    role: MembershipRole
    expires_at: datetime | None = None
    must_change_password: bool = False


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class MeResponse(BaseModel):
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: MembershipRole
    email: str
    must_change_password: bool


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, session: AsyncSession = Depends(get_db_session)) -> TokenResponse:
    try:
        user, membership = await saas_service.authenticate_user(session, payload.email, payload.password, payload.org_id)
    except ValueError as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    session_record, refresh_token = await saas_service.create_session(
        session,
        user,
        membership,
        ttl_minutes=settings.auth_session_ttl_minutes,
        refresh_ttl_minutes=settings.auth_refresh_token_ttl_minutes,
    )
    token = saas_service.build_session_access_token(user, membership, session_record.session_id)
    await session.commit()
    return TokenResponse(
        access_token=token,
        refresh_token=refresh_token,
        org_id=membership.org_id,
        role=membership.role,
        expires_at=session_record.expires_at,
        must_change_password=user.must_change_password,
    )


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/refresh", response_model=TokenResponse)
async def refresh_tokens(payload: RefreshRequest, session: AsyncSession = Depends(get_db_session)) -> TokenResponse:
    try:
        access_token, refresh_token, token_session, membership = await saas_service.refresh_tokens(
            session, payload.refresh_token
        )
    except ValueError as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    user = await session.get(User, membership.user_id)
    await session.commit()
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        org_id=membership.org_id,
        role=membership.role,
        expires_at=token_session.expires_at,
        must_change_password=bool(getattr(user, "must_change_password", False)),
    )


@router.post("/logout")
async def logout(
    identity=Depends(require_saas_user), session: AsyncSession = Depends(get_db_session)
) -> dict[str, str]:
    session_id = getattr(identity, "session_id", None)
    if session_id:
        await saas_service.revoke_session(session, session_id, reason="logout")
        await session.commit()
    return {"status": "ok"}


def _assert_password_policy(password: str) -> None:
    if len(password) < 12:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password too short")
    if not any(ch.islower() for ch in password) or not any(ch.isupper() for ch in password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must include upper and lower case letters")
    if not any(ch.isdigit() for ch in password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must include a digit")


@router.post("/change-password", response_model=TokenResponse)
async def change_password(
    payload: ChangePasswordRequest,
    identity=Depends(require_saas_user),
    session: AsyncSession = Depends(get_db_session),
) -> TokenResponse:
    user = await session.get(User, identity.user_id)
    membership = await session.scalar(
        sa.select(Membership).where(
            Membership.user_id == identity.user_id,
            Membership.org_id == identity.org_id,
            Membership.is_active.is_(True),
        )
    )
    if not user or not membership or not user.password_hash:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    valid, upgraded = saas_service.verify_password(payload.current_password, user.password_hash, settings=settings)
    if not valid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if upgraded and upgraded != user.password_hash:
        user.password_hash = upgraded
    _assert_password_policy(payload.new_password)
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New password must differ from current")

    await saas_service.set_new_password(session, user, payload.new_password)
    await saas_service.revoke_user_sessions(session, user.user_id, reason="password_changed")

    session_record, refresh_token = await saas_service.create_session(
        session,
        user,
        membership,
        ttl_minutes=settings.auth_session_ttl_minutes,
        refresh_ttl_minutes=settings.auth_refresh_token_ttl_minutes,
    )
    token = saas_service.build_session_access_token(user, membership, session_record.session_id)
    await session.commit()
    return TokenResponse(
        access_token=token,
        refresh_token=refresh_token,
        org_id=membership.org_id,
        role=membership.role,
        expires_at=session_record.expires_at,
        must_change_password=user.must_change_password,
    )


@router.get("/me", response_model=MeResponse)
async def me(identity=Depends(require_saas_user)) -> MeResponse:
    return MeResponse(
        user_id=identity.user_id,
        org_id=identity.org_id,
        role=identity.role,
        email=identity.email,
        must_change_password=getattr(identity, "must_change_password", False),
    )


class OrgContextResponse(BaseModel):
    org_id: uuid.UUID


@router.get("/org-context", response_model=OrgContextResponse)
async def org_context(org_id: uuid.UUID = Depends(require_org_context)) -> OrgContextResponse:
    return OrgContextResponse(org_id=org_id)


class MembershipResponse(BaseModel):
    membership_id: int
    org_id: uuid.UUID
    user_id: uuid.UUID
    role: MembershipRole
    is_active: bool


class MemberListResponse(BaseModel):
    members: list[MembershipResponse]


@router.get("/orgs/{org_id}/members", response_model=MemberListResponse)
async def list_members(
    org_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    identity=Depends(require_permissions(AdminPermission.ADMIN)),
) -> MemberListResponse:
    if identity.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    result = await session.execute(
        sa.select(Membership).where(Membership.org_id == org_id, Membership.is_active.is_(True))
    )
    members = result.scalars().all()
    return MemberListResponse(
        members=[
            MembershipResponse(
                membership_id=m.membership_id,
                org_id=m.org_id,
                user_id=m.user_id,
                role=m.role,
                is_active=m.is_active,
            )
            for m in members
        ]
    )
