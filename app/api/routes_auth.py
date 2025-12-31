import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_auth import AdminPermission
from app.api.saas_auth import require_permissions
from app.domain.saas import service as saas_service
from app.domain.saas.db_models import Membership, MembershipRole
from app.infra.db import get_db_session

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str
    org_id: uuid.UUID | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    org_id: uuid.UUID
    role: MembershipRole


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, session: AsyncSession = Depends(get_db_session)) -> TokenResponse:
    try:
        user, membership = await saas_service.authenticate_user(session, payload.email, payload.password, payload.org_id)
    except ValueError as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    token = saas_service.build_access_token(user, membership)
    return TokenResponse(access_token=token, org_id=membership.org_id, role=membership.role)


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
