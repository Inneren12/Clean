import logging

from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request, status

from app.api.problem_details import problem_details

logger = logging.getLogger(__name__)


class AdminMfaMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, app_settings):  # type: ignore[override]
        super().__init__(app)
        self.app_settings = app_settings
        self._required_roles = {role.lower() for role in getattr(app_settings, "admin_mfa_required_roles", [])}

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        path = request.url.path
        if not (path.startswith("/v1/admin") or path.startswith("/v1/iam")):
            return await call_next(request)
        if not getattr(self.app_settings, "admin_mfa_required", False):
            return await call_next(request)

        identity = getattr(request.state, "saas_identity", None)
        if identity is None:
            return await call_next(request)

        role = getattr(getattr(identity, "role", None), "value", "")
        if role.lower() not in self._required_roles:
            return await call_next(request)

        if getattr(identity, "mfa_verified", False):
            return await call_next(request)

        logger.info("admin_mfa_required", extra={"extra": {"role": role, "path": path}})
        return problem_details(
            request=request,
            status=status.HTTP_401_UNAUTHORIZED,
            title="Unauthorized",
            detail="MFA verification required",
            type_="mfa_required",
        )
