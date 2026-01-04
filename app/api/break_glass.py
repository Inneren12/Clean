import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_auth import AdminIdentity, AdminPermission, require_permissions
from app.domain.admin_audit import service as audit_service
from app.domain.break_glass import service as break_glass_service
from app.domain.break_glass.db_models import BreakGlassSession
from app.infra.db import get_db_session
from app.settings import settings

router = APIRouter()

BREAK_GLASS_HEADER = "X-Break-Glass-Token"


class BreakGlassStartRequest(BaseModel):
    reason: str = Field(..., min_length=1)
    ttl_minutes: int | None = Field(None, ge=1, le=break_glass_service.MAX_TTL_MINUTES)

    @field_validator("reason")
    @classmethod
    def _trim_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("reason cannot be empty")
        return cleaned


class BreakGlassStartResponse(BaseModel):
    token: str
    expires_at: datetime


async def require_break_glass(
    request: Request, session: AsyncSession = Depends(get_db_session)
) -> BreakGlassSession:
    cached: BreakGlassSession | None = getattr(request.state, "break_glass_session", None)
    if cached:
        return cached

    token = request.headers.get(BREAK_GLASS_HEADER)
    if not token:
        raise HTTPException(status_code=403, detail="Break-glass token required")

    org_id = getattr(request.state, "current_org_id", None) or settings.default_org_id
    record = await break_glass_service.get_valid_session(session, org_id=org_id, token=token)
    if record is None:
        raise HTTPException(status_code=403, detail="Break-glass token invalid or expired")

    set_break_glass_state(request, record)
    return record


def set_break_glass_state(request: Request, record: BreakGlassSession) -> None:
    request.state.break_glass = True
    request.state.break_glass_reason = record.reason
    request.state.break_glass_session = record
    request.state.break_glass_session_id = record.session_id


@router.post(
    "/v1/admin/break-glass/start", response_model=BreakGlassStartResponse, status_code=status.HTTP_200_OK
)
async def start_break_glass(
    payload: BreakGlassStartRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    identity: AdminIdentity = Depends(require_permissions(AdminPermission.ADMIN)),
) -> BreakGlassStartResponse:
    org_id: uuid.UUID = getattr(request.state, "current_org_id", None) or settings.default_org_id
    token, record = await break_glass_service.create_session(
        session,
        org_id=org_id,
        actor=identity.username,
        reason=payload.reason,
        ttl_minutes=payload.ttl_minutes,
    )

    await audit_service.record_action(
        session,
        identity=identity,
        action="break_glass_start",
        resource_type="break_glass_session",
        resource_id=str(record.session_id),
        before=None,
        after={"reason": payload.reason, "expires_at": record.expires_at.isoformat()},
    )
    await session.commit()

    return BreakGlassStartResponse(token=token, expires_at=record.expires_at)
