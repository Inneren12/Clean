import logging
import os
import sys
import time
import uuid
from typing import Callable, Iterable

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.admin_auth import AdminAccessMiddleware, AdminAuditMiddleware
from app.api.routes_admin import router as admin_router
from app.api.routes_bookings import router as bookings_router
from app.api.routes_chat import router as chat_router
from app.api.routes_estimate import router as estimate_router
from app.api.routes_bot import router as bot_router
from app.api.routes_checklists import router as checklists_router
from app.api.routes_health import router as health_router
from app.api.routes_client import router as client_router
from app.api.routes_payments import router as payments_router
from app.api.routes_orders import router as orders_router
from app.api.routes_time_tracking import router as time_tracking_router
from app.api.routes_ui_lang import router as ui_lang_router
from app.api.routes_worker import router as worker_router
from app.api.routes_auth import router as auth_router
from app.api.worker_auth import WorkerAccessMiddleware
from app.api.routes_public import router as public_router
from app.api.routes_leads import router as leads_router
from app.api.routes_billing import router as billing_router
from app.api.saas_auth import TenantSessionMiddleware
from app.domain.errors import DomainError
from app.infra.db import get_session_factory
from app.infra.email import EmailAdapter, resolve_email_adapter
from app.infra.logging import configure_logging
from app.infra.security import RateLimiter, create_rate_limiter, resolve_client_key
from app.settings import settings

PROBLEM_TYPE_VALIDATION = "https://example.com/problems/validation-error"
PROBLEM_TYPE_DOMAIN = "https://example.com/problems/domain-error"
PROBLEM_TYPE_RATE_LIMIT = "https://example.com/problems/rate-limit"
PROBLEM_TYPE_SERVER = "https://example.com/problems/server-error"

logger = logging.getLogger(__name__)


def problem_details(
    request: Request,
    status: int,
    title: str,
    detail: str,
    errors: list[dict[str, str]] | None = None,
    type_: str = "about:blank",
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    content = {
        "type": type_,
        "title": title,
        "status": status,
        "detail": detail,
        "request_id": getattr(request.state, "request_id", None),
        "errors": errors or [],
    }
    return JSONResponse(status_code=status, content=content, headers=headers)


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        logger = logging.getLogger("app.request")
        start = time.time()
        response = await call_next(request)
        latency_ms = int((time.time() - start) * 1000)
        logger.info(
            "request",
            extra={
                "request_id": request.state.request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "latency_ms": latency_ms,
            },
        )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'",
        )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: FastAPI, limiter: RateLimiter, app_settings) -> None:
        super().__init__(app)
        self.limiter = limiter
        self.app_settings = app_settings

    async def dispatch(self, request: Request, call_next: Callable):
        client = resolve_client_key(
            request,
            trust_proxy_headers=self.app_settings.trust_proxy_headers,
            trusted_proxy_ips=self.app_settings.trusted_proxy_ips,
            trusted_proxy_cidrs=self.app_settings.trusted_proxy_cidrs,
        )
        if not await self.limiter.allow(client):
            return problem_details(
                request=request,
                status=429,
                title="Too Many Requests",
                detail="Rate limit exceeded",
                type_=PROBLEM_TYPE_RATE_LIMIT,
            )
        return await call_next(request)


def _resolve_cors_origins(app_settings) -> Iterable[str]:
    if app_settings.cors_origins:
        return app_settings.cors_origins
    if app_settings.strict_cors:
        return []
    if app_settings.app_env == "dev":
        return ["http://localhost:3000"]
    return []


def _try_include_style_guide(app: FastAPI) -> None:
    try:
        from app.api.routes_style_guide import router as style_guide_router
    except Exception as exc:
        logger.warning("style_guide_disabled", extra={"extra": {"reason": str(exc)}})
        return
    app.include_router(style_guide_router)


def _validate_prod_config(app_settings) -> None:
    if app_settings.app_env == "dev" or getattr(app_settings, "testing", False) or os.getenv("PYTEST_CURRENT_TEST") or "pytest" in sys.argv[0]:
        return

    errors: list[str] = []
    if not app_settings.worker_portal_secret:
        errors.append("WORKER_PORTAL_SECRET is required outside dev")

    admin_credentials = [
        (app_settings.owner_basic_username, app_settings.owner_basic_password),
        (app_settings.admin_basic_username, app_settings.admin_basic_password),
        (app_settings.dispatcher_basic_username, app_settings.dispatcher_basic_password),
        (app_settings.accountant_basic_username, app_settings.accountant_basic_password),
        (app_settings.viewer_basic_username, app_settings.viewer_basic_password),
    ]
    if not any(username and password for username, password in admin_credentials):
        errors.append("At least one admin credential pair must be configured outside dev")

    if errors:
        for error in errors:
            logger.error("startup_config_error", extra={"extra": {"detail": error}})
        raise RuntimeError("Invalid production configuration; see logs for details")


def create_app(app_settings) -> FastAPI:
    configure_logging()
    _validate_prod_config(app_settings)
    app = FastAPI(title="Cleaning Economy Bot", version="1.0.0")

    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    rate_limiter = create_rate_limiter(app_settings)
    app.state.rate_limiter = rate_limiter
    app.state.app_settings = app_settings
    app.state.db_session_factory = get_session_factory()
    app.state.export_transport = None
    app.state.export_resolver = None
    app.state.email_adapter = resolve_email_adapter(app_settings)
    app.state.stripe_client = None

    @app.on_event("shutdown")
    async def shutdown_limiter() -> None:
        await rate_limiter.close()

    app.add_middleware(RateLimitMiddleware, limiter=rate_limiter, app_settings=app_settings)
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(TenantSessionMiddleware)
    app.add_middleware(AdminAccessMiddleware)
    app.add_middleware(AdminAuditMiddleware)
    app.add_middleware(WorkerAccessMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(_resolve_cors_origins(app_settings)),
        allow_methods=["*"],
        allow_headers=["*"],
    )


    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        errors = []
        for error in exc.errors():
            loc = error.get("loc", [])
            field = ".".join(str(part) for part in loc if part not in {"body", "query", "path"}) or "body"
            errors.append({"field": field, "message": error.get("msg", "Invalid value")})
        return problem_details(
            request=request,
            status=422,
            title="Validation Error",
            detail="Request validation failed",
            errors=errors,
            type_=PROBLEM_TYPE_VALIDATION,
        )


    @app.exception_handler(DomainError)
    async def domain_exception_handler(request: Request, exc: DomainError):
        return problem_details(
            request=request,
            status=400,
            title=exc.title,
            detail=exc.detail,
            errors=exc.errors or [],
            type_=exc.type or PROBLEM_TYPE_DOMAIN,
        )


    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        return problem_details(
            request=request,
            status=exc.status_code,
            title=exc.detail if isinstance(exc.detail, str) else "HTTP Error",
            detail=exc.detail if isinstance(exc.detail, str) else "Request failed",
            type_=PROBLEM_TYPE_DOMAIN if exc.status_code < 500 else PROBLEM_TYPE_SERVER,
            headers=exc.headers,
        )


    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception(
            "unhandled_exception",
            extra={"request_id": getattr(request.state, "request_id", None), "path": request.url.path},
        )
        return problem_details(
            request=request,
            status=500,
            title="Internal Server Error",
            detail="Unexpected error",
            type_=PROBLEM_TYPE_SERVER,
        )


    app.include_router(health_router)
    app.include_router(public_router)
    app.include_router(auth_router)
    app.include_router(bot_router)
    app.include_router(estimate_router)
    app.include_router(chat_router)
    app.include_router(client_router)
    app.include_router(payments_router)
    app.include_router(billing_router)
    app.include_router(orders_router)
    app.include_router(checklists_router)
    app.include_router(time_tracking_router)
    app.include_router(ui_lang_router)
    app.include_router(worker_router)
    app.include_router(bookings_router)
    app.include_router(leads_router)
    app.include_router(admin_router)
    _try_include_style_guide(app)
    return app


app = create_app(settings)
