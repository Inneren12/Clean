import base64
import logging
import secrets
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.security.utils import get_authorization_scheme_param
from starlette.middleware.base import BaseHTTPMiddleware

from app.settings import settings
from app.infra.logging import update_log_context
from app.infra.org_context import set_current_org_id

logger = logging.getLogger(__name__)


class AdminRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    DISPATCHER = "dispatcher"
    ACCOUNTANT = "accountant"
    FINANCE = "finance"
    VIEWER = "viewer"


class AdminPermission(str, Enum):
    VIEW = "view"
    DISPATCH = "dispatch"
    FINANCE = "finance"
    ADMIN = "admin"


ROLE_PERMISSIONS: dict[AdminRole, set[AdminPermission]] = {
    AdminRole.OWNER: {AdminPermission.VIEW, AdminPermission.DISPATCH, AdminPermission.FINANCE, AdminPermission.ADMIN},
    AdminRole.ADMIN: {AdminPermission.VIEW, AdminPermission.DISPATCH, AdminPermission.FINANCE, AdminPermission.ADMIN},
    AdminRole.DISPATCHER: {AdminPermission.VIEW, AdminPermission.DISPATCH},
    AdminRole.ACCOUNTANT: {AdminPermission.VIEW, AdminPermission.FINANCE},
    AdminRole.FINANCE: {AdminPermission.VIEW, AdminPermission.FINANCE},
    AdminRole.VIEWER: {AdminPermission.VIEW},
}


@dataclass
class AdminIdentity:
    username: str
    role: AdminRole
    org_id: uuid.UUID | None = None


@dataclass
class _ConfiguredUser:
    username: str
    password: str
    role: AdminRole


security = HTTPBasic(auto_error=False)


def _configured_users() -> list[_ConfiguredUser]:
    configured: list[_ConfiguredUser] = []
    if settings.owner_basic_username and settings.owner_basic_password:
        configured.append(
            _ConfiguredUser(
                username=settings.owner_basic_username,
                password=settings.owner_basic_password,
                role=AdminRole.OWNER,
            )
        )
    if settings.admin_basic_username and settings.admin_basic_password:
        configured.append(
            _ConfiguredUser(
                username=settings.admin_basic_username,
                password=settings.admin_basic_password,
                role=AdminRole.ADMIN,
            )
        )
    if settings.dispatcher_basic_username and settings.dispatcher_basic_password:
        configured.append(
            _ConfiguredUser(
                username=settings.dispatcher_basic_username,
                password=settings.dispatcher_basic_password,
                role=AdminRole.DISPATCHER,
            )
        )
    if settings.accountant_basic_username and settings.accountant_basic_password:
        configured.append(
            _ConfiguredUser(
                username=settings.accountant_basic_username,
                password=settings.accountant_basic_password,
                role=AdminRole.ACCOUNTANT,
            )
        )
    if settings.viewer_basic_username and settings.viewer_basic_password:
        configured.append(
            _ConfiguredUser(
                username=settings.viewer_basic_username,
                password=settings.viewer_basic_password,
                role=AdminRole.VIEWER,
            )
        )
    return configured


def _build_auth_exception(detail: str = "Invalid authentication") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Basic"},
    )


def _authenticate_credentials(credentials: HTTPBasicCredentials | None) -> AdminIdentity:
    configured = _configured_users()
    if not configured:
        logger.warning(
            "admin_auth_unconfigured",
            extra={
                "extra": {
                    "path": "/v1/admin",
                    "method": "BASIC",
                    "owner_configured": bool(settings.owner_basic_username and settings.owner_basic_password),
                    "admin_configured": bool(settings.admin_basic_username and settings.admin_basic_password),
                    "dispatcher_configured": bool(
                        settings.dispatcher_basic_username and settings.dispatcher_basic_password
                    ),
                    "accountant_configured": bool(settings.accountant_basic_username and settings.accountant_basic_password),
                    "viewer_configured": bool(settings.viewer_basic_username and settings.viewer_basic_password),
                }
            },
        )
        raise _build_auth_exception()

    if not credentials:
        raise _build_auth_exception()

    for user in configured:
        if secrets.compare_digest(credentials.username, user.username) and secrets.compare_digest(
            credentials.password, user.password
        ):
            return AdminIdentity(
                username=user.username, role=user.role, org_id=settings.default_org_id
            )

    raise _build_auth_exception()


def _assert_permissions(identity: AdminIdentity, required: Iterable[AdminPermission]) -> None:
    granted = ROLE_PERMISSIONS.get(identity.role, set())
    missing = set(required) - granted
    if missing:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


def _credentials_from_header(request: Request) -> HTTPBasicCredentials | None:
    authorization: str | None = request.headers.get("Authorization")
    scheme, param = get_authorization_scheme_param(authorization)
    if not authorization or scheme.lower() != "basic":
        return None
    try:
        decoded = base64.b64decode(param).decode("latin1")
    except Exception:  # noqa: BLE001
        raise _build_auth_exception()
    username, _, password = decoded.partition(":")
    if not username:
        raise _build_auth_exception()
    return HTTPBasicCredentials(username=username, password=password)


async def get_admin_identity(
    request: Request, credentials: HTTPBasicCredentials | None = Depends(security)
) -> AdminIdentity:
    cached: AdminIdentity | None = getattr(request.state, "admin_identity", None)
    if cached:
        request.state.current_org_id = getattr(request.state, "current_org_id", None) or cached.org_id
        set_current_org_id(request.state.current_org_id)
        org_for_log = request.state.current_org_id
        payload: dict[str, str] = {"role": getattr(cached.role, "value", str(cached.role))}
        if org_for_log:
            payload["org_id"] = str(org_for_log)
        update_log_context(**payload)
        return cached
    identity = _authenticate_credentials(credentials)
    request.state.admin_identity = identity
    request.state.current_org_id = getattr(request.state, "current_org_id", None) or identity.org_id
    set_current_org_id(request.state.current_org_id)
    org_for_log = request.state.current_org_id
    payload = {"role": getattr(identity.role, "value", str(identity.role))}
    if org_for_log:
        payload["org_id"] = str(org_for_log)
    update_log_context(**payload)
    return identity


def require_permissions(*permissions: AdminPermission):
    async def _require(identity: AdminIdentity = Depends(get_admin_identity)) -> AdminIdentity:
        _assert_permissions(identity, permissions or [AdminPermission.VIEW])
        return identity

    return _require


async def require_admin(identity: AdminIdentity = Depends(require_permissions(AdminPermission.ADMIN))) -> AdminIdentity:
    return identity


async def require_dispatch(identity: AdminIdentity = Depends(require_permissions(AdminPermission.DISPATCH))) -> AdminIdentity:
    return identity


async def require_finance(
    identity: AdminIdentity = Depends(require_permissions(AdminPermission.FINANCE)),
) -> AdminIdentity:
    return identity


async def require_viewer(
    identity: AdminIdentity = Depends(require_permissions(AdminPermission.VIEW)),
) -> AdminIdentity:
    return identity


async def verify_admin_or_dispatcher(
    identity: AdminIdentity = Depends(require_permissions(AdminPermission.DISPATCH)),
) -> AdminIdentity:
    return identity


class AdminAccessMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if not request.url.path.startswith("/v1/admin"):
            return await call_next(request)

        cached: AdminIdentity | None = getattr(request.state, "admin_identity", None)
        if cached:
            return await call_next(request)

        saas_identity = getattr(request.state, "saas_identity", None)
        if saas_identity:
            return await call_next(request)

        saas_identity_error: HTTPException | None = getattr(request.state, "saas_identity_error", None)
        authorization: str = request.headers.get("Authorization", "")
        has_bearer = authorization.lower().startswith("bearer ")

        if saas_identity_error:
            return await http_exception_handler(request, saas_identity_error)

        if has_bearer:
            unauthorized = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
            return await http_exception_handler(request, unauthorized)

        if not settings.legacy_basic_auth_enabled:
            return await http_exception_handler(request, _build_auth_exception())

        try:
            credentials = _credentials_from_header(request)
            identity = _authenticate_credentials(credentials)
            _assert_permissions(identity, [AdminPermission.VIEW])
            request.state.admin_identity = identity
            request.state.current_org_id = getattr(request.state, "current_org_id", None) or identity.org_id
            set_current_org_id(request.state.current_org_id)
            return await call_next(request)
        except HTTPException as exc:
            return await http_exception_handler(request, exc)


class AdminAuditMiddleware(BaseHTTPMiddleware):
    SENSITIVE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        should_audit = request.url.path.startswith("/v1/admin") and request.method in self.SENSITIVE_METHODS
        body_bytes: bytes | None = None
        if should_audit:
            body_bytes = await request.body()
            request._body = body_bytes

        request.state.explicit_admin_audit = False
        response = await call_next(request)
        already_logged = getattr(request.state, "explicit_admin_audit", False)

        if should_audit and not already_logged:
            from app.domain.admin_audit import service as audit_service

            identity: AdminIdentity | None = getattr(request.state, "admin_identity", None)
            if identity is None:
                return response

            before = _safe_json(body_bytes)
            raw_after = getattr(response, "body", None)
            after = _safe_json(raw_after if isinstance(raw_after, (bytes, bytearray)) else None)
            session_factory = getattr(request.app.state, "db_session_factory", None)
            if session_factory is None:
                return response
            try:
                async with session_factory() as session:
                    await audit_service.record_action(
                        session,
                        identity=identity,
                        action=f"{request.method} {request.url.path}",
                        resource_type=None,
                        resource_id=None,
                        before=before,
                        after=after,
                    )
                    await session.commit()
            except Exception:  # noqa: BLE001
                logger.exception("admin_audit_failed")
                return response
        return response


def _safe_json(payload: Optional[bytes]) -> dict | list | None:
    if not payload:
        return None
    try:
        import json

        return json.loads(payload.decode())
    except Exception:  # noqa: BLE001
        return None
