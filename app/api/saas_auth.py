import logging
import uuid
from dataclasses import dataclass
from typing import Iterable

import sqlalchemy as sa
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.admin_auth import AdminIdentity, AdminPermission, AdminRole, ROLE_PERMISSIONS
from app.api.problem_details import problem_details
from app.domain.saas import service as saas_service
from app.domain.saas.db_models import Membership, MembershipRole, User
from app.infra.auth import decode_access_token
from app.infra.logging import update_log_context
from app.infra.org_context import set_current_org_id

logger = logging.getLogger(__name__)

bearer_security = HTTPBearer(auto_error=False)
ALLOW_WHILE_MUST_CHANGE = {
    "/v1/auth/change-password",
    "/v1/auth/logout",
    "/v1/auth/me",
    "/v1/auth/refresh",
}


@dataclass
class SaaSIdentity:
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: MembershipRole
    email: str
    must_change_password: bool = False
    session_id: uuid.UUID | None = None
    mfa_verified: bool = False


ROLE_TO_ADMIN_ROLE: dict[MembershipRole, AdminRole] = {
    MembershipRole.OWNER: AdminRole.OWNER,
    MembershipRole.ADMIN: AdminRole.ADMIN,
    MembershipRole.DISPATCHER: AdminRole.DISPATCHER,
    MembershipRole.FINANCE: AdminRole.FINANCE,
    MembershipRole.VIEWER: AdminRole.VIEWER,
}


def _get_cached_identity(request: Request) -> "SaaSIdentity | None":
    return getattr(request.state, "saas_identity", None)


def _get_saas_token(request: Request) -> str | None:
    authorization: str | None = request.headers.get("Authorization")
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1]
    return request.cookies.get("saas_session")


async def _load_identity(request: Request, token: str | None, *, strict: bool = False) -> SaaSIdentity | None:
    if not token:
        if strict:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing SaaS token")
        return None
    try:
        payload = decode_access_token(token, request.app.state.app_settings.auth_secret_key)
    except Exception:  # noqa: BLE001
        logger.info("saas_token_invalid")
        if strict:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid SaaS token")
        return None

    try:
        user_id = uuid.UUID(str(payload.get("sub")))
        org_id = uuid.UUID(str(payload.get("org_id")))
        role_raw = payload.get("role")
        role = MembershipRole(role_raw)
        session_id_raw = payload.get("sid")
        session_id = uuid.UUID(str(session_id_raw)) if session_id_raw else None
        mfa_verified = bool(payload.get("mfa", False))
    except Exception:  # noqa: BLE001
        logger.info("saas_token_payload_invalid")
        if strict:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid SaaS token payload")
        return None

    session_factory = getattr(request.app.state, "db_session_factory", None)
    if not session_factory:
        return SaaSIdentity(user_id=user_id, org_id=org_id, role=role, email="", session_id=session_id)

    async with session_factory() as session:
        if session_id:
            session_record = await saas_service.validate_session_record(session, session_id)
            if not session_record:
                if strict:
                    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
                return None
            mfa_verified = bool(session_record.mfa_verified)
        result = await session.execute(
            sa.select(
                User.email,
                User.is_active,
                User.must_change_password,
                Membership.role,
                Membership.is_active,
            )
            .join(Membership, Membership.user_id == User.user_id)
            .where(
                User.user_id == user_id,
                Membership.org_id == org_id,
            )
        )
        row = result.first()
        if not row:
            if strict:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
            return None
        email, user_active, must_change_password, membership_role, is_active = row
        if not user_active or not is_active or membership_role != role:
            if strict:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
            return None
        return SaaSIdentity(
            user_id=user_id,
            org_id=org_id,
            role=role,
            email=email,
            must_change_password=bool(must_change_password),
            session_id=session_id,
            mfa_verified=mfa_verified,
        )


class TenantSessionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        token = _get_saas_token(request)
        identity = None
        identity_error: HTTPException | None = None

        if token:
            try:
                identity = await _load_identity(request, token, strict=True)
            except HTTPException as exc:
                identity_error = exc
        else:
            identity = await _load_identity(request, token)

        if identity:
            request.state.saas_identity = identity
            request.state.current_user_id = identity.user_id
            request.state.current_org_id = identity.org_id
            set_current_org_id(identity.org_id)
            update_log_context(org_id=str(identity.org_id), user_id=str(identity.user_id), role=str(identity.role))
            admin_role = ROLE_TO_ADMIN_ROLE.get(identity.role)
            if admin_role:
                request.state.admin_identity = AdminIdentity(
                    username=identity.email or str(identity.user_id),
                    role=admin_role,
                    org_id=identity.org_id,
                )
        elif identity_error:
            request.state.saas_identity_error = identity_error
        return await call_next(request)


class PasswordChangeGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        identity = getattr(request.state, "saas_identity", None)
        if identity and not getattr(identity, "must_change_password", False):
            session_factory = getattr(request.app.state, "db_session_factory", None)
            if session_factory:
                async with session_factory() as session:
                    must_change = await session.scalar(
                        sa.select(User.must_change_password).where(User.user_id == identity.user_id)
                    )
                    if must_change:
                        identity.must_change_password = True

        if identity and getattr(identity, "must_change_password", False):
            path = request.url.path
            if not any(path.startswith(prefix) for prefix in ALLOW_WHILE_MUST_CHANGE):
                return problem_details(
                    request,
                    status=status.HTTP_403_FORBIDDEN,
                    title="Forbidden",
                    detail="Password change required",
                )
        return await call_next(request)


def require_saas_user(
    request: Request, identity: SaaSIdentity | None = Depends(_get_cached_identity)
):
    if not identity:
        error_from_state: HTTPException | None = getattr(request.state, "saas_identity_error", None)
        if error_from_state:
            raise error_from_state
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    request.state.current_org_id = identity.org_id
    set_current_org_id(identity.org_id)
    return identity


def require_org(org_id: uuid.UUID):
    async def _require(identity: SaaSIdentity = Depends(require_saas_user)) -> SaaSIdentity:
        if identity.org_id != org_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        return identity

    return _require


def require_role(*roles: MembershipRole):
    async def _require(identity: SaaSIdentity = Depends(require_saas_user)) -> SaaSIdentity:
        if roles and identity.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        return identity

    return _require


def require_permissions(*permissions: AdminPermission):
    async def _require(identity: SaaSIdentity = Depends(require_saas_user)) -> SaaSIdentity:
        admin_role = ROLE_TO_ADMIN_ROLE.get(identity.role)
        if not admin_role:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        granted = ROLE_PERMISSIONS.get(admin_role, set())
        missing = set(permissions) - granted
        if missing:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        return identity

    return _require
