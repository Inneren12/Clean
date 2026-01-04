import logging
import uuid
from ipaddress import ip_address, ip_network

from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request

from app.api.problem_details import problem_details
from app.api.problem_details import PROBLEM_TYPE_DOMAIN
from app.infra.security import resolve_client_key

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

        if request.method in self.WRITE_METHODS and getattr(
            self.app_settings, "admin_read_only", False
        ):
            self._log_denial(request, reason="read_only", client_ip=client_ip)
            return problem_details(
                request=request,
                status=409,
                title="Conflict",
                detail="Admin writes temporarily disabled",
                type_=PROBLEM_TYPE_DOMAIN,
            )

        return await call_next(request)

    def _is_protected_path(self, path: str) -> bool:
        return path.startswith("/v1/admin") or path.startswith("/v1/iam")

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
