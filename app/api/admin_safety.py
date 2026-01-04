import logging
import uuid
from ipaddress import ip_address, ip_network

from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request

from app.api.break_glass import BREAK_GLASS_HEADER, set_break_glass_state
from app.api.problem_details import PROBLEM_TYPE_DOMAIN, problem_details
from app.domain.admin_audit import service as audit_service
from app.domain.break_glass import service as break_glass_service
from app.infra.security import resolve_client_key
from app.settings import settings

logger = logging.getLogger(__name__)


class AdminSafetyMiddleware(BaseHTTPMiddleware):
    WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

    def __init__(self, app, app_settings) -> None:  # type: ignore[override]
        super().__init__(app)
        self.app_settings = app_settings

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        path = request.url.path
        if not self._is_protected_path(path):
            return await call_next(request)

        client_ip = resolve_client_key(
            request,
            trust_proxy_headers=self.app_settings.trust_proxy_headers,
            trusted_proxy_ips=self.app_settings.trusted_proxy_ips,
            trusted_proxy_cidrs=self.app_settings.trusted_proxy_cidrs,
        )

        if self._is_ip_blocked(client_ip):
            self._log_denial(request, reason="ip_denied", client_ip=client_ip)
            return problem_details(
                request=request,
                status=403,
                title="Forbidden",
                detail="Admin access restricted to allowlisted IPs",
                type_=PROBLEM_TYPE_DOMAIN,
            )

        break_glass_session = None
        enforcing_read_only = request.method in self.WRITE_METHODS and getattr(
            self.app_settings, "admin_read_only", False
        )

        if enforcing_read_only and not self._is_allowlisted_path(path):
            break_glass_session = await self._validate_break_glass(request)
            if break_glass_session is None:
                self._log_denial(request, reason="read_only", client_ip=client_ip)
                return problem_details(
                    request=request,
                    status=409,
                    title="Conflict",
                    detail="Admin writes temporarily disabled",
                    type_=PROBLEM_TYPE_DOMAIN,
                )

        response = await call_next(request)

        if break_glass_session is not None:
            await self._audit_break_glass_write(request, break_glass_session)

        return response

    def _is_protected_path(self, path: str) -> bool:
        return path.startswith("/v1/admin") or path.startswith("/v1/iam")

    def _is_allowlisted_path(self, path: str) -> bool:
        return path.startswith("/v1/admin/break-glass/start")

    def _is_ip_blocked(self, client_ip: str) -> bool:
        cidrs = getattr(self.app_settings, "admin_ip_allowlist_cidrs", [])
        if not cidrs:
            return False
        try:
            ip = ip_address(client_ip)
        except ValueError:
            return True

        for cidr in cidrs:
            try:
                if ip in ip_network(cidr, strict=False):
                    return False
            except ValueError:
                logger.warning(
                    "admin_ip_allowlist_invalid_cidr", extra={"extra": {"cidr": cidr}}
                )
        return True

    def _log_denial(self, request: Request, *, reason: str, client_ip: str) -> None:
        request_id = (
            getattr(request.state, "request_id", None)
            or request.headers.get("X-Request-ID")
            or str(uuid.uuid4())
        )
        request.state.request_id = request_id
        org_id = getattr(request.state, "current_org_id", None)
        role = None

        saas_identity = getattr(request.state, "saas_identity", None)
        admin_identity = getattr(request.state, "admin_identity", None)
        worker_identity = getattr(request.state, "worker_identity", None)

        if saas_identity:
            role = getattr(getattr(saas_identity, "role", None), "value", None)
            org_id = org_id or getattr(saas_identity, "org_id", None)
        elif admin_identity:
            role = getattr(getattr(admin_identity, "role", None), "value", None)
            org_id = org_id or getattr(admin_identity, "org_id", None)
        elif worker_identity:
            role = "worker"
            org_id = org_id or getattr(worker_identity, "org_id", None)

        context: dict[str, str] = {
            "request_id": request_id,
            "path": request.url.path,
            "method": request.method,
            "reason": reason,
            "client_ip": client_ip,
        }
        if org_id:
            context["org_id"] = str(org_id)
        if role:
            context["role"] = str(role)

        logger.warning("admin_safety_denied", extra={"extra": context})

    async def _validate_break_glass(self, request: Request):
        token = request.headers.get(BREAK_GLASS_HEADER)
        if not token:
            return None

        org_id = getattr(request.state, "current_org_id", None) or settings.default_org_id
        session_factory = getattr(request.app.state, "db_session_factory", None)
        if session_factory is None:
            logger.warning("break_glass_validation_missing_session_factory")
            return None

        try:
            async with session_factory() as session:
                record = await break_glass_service.get_valid_session(
                    session, org_id=org_id, token=token
                )
        except Exception:  # noqa: BLE001
            logger.exception("break_glass_validation_failed")
            return None

        if record is None:
            return None

        set_break_glass_state(request, record)
        return record

    async def _audit_break_glass_write(self, request: Request, record) -> None:
        identity = getattr(request.state, "admin_identity", None)
        session_factory = getattr(request.app.state, "db_session_factory", None)
        if identity is None or session_factory is None:
            return

        try:
            async with session_factory() as session:
                await audit_service.record_action(
                    session,
                    identity=identity,
                    action="break_glass_write",
                    resource_type="http_request",
                    resource_id=f"{request.method} {request.url.path}",
                    before=None,
                    after={
                        "reason": getattr(request.state, "break_glass_reason", record.reason),
                        "session_id": str(getattr(record, "session_id", "")),
                    },
                )
                await session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("break_glass_audit_failed")
