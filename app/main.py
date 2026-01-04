import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from typing import Callable, Iterable

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.admin_auth import AdminAccessMiddleware, AdminAuditMiddleware
from app.api.routes_admin import router as admin_router
from app.api.break_glass import router as break_glass_router
from app.api.routes_queues import router as queues_router
from app.api.routes_timeline import router as timeline_router
from app.api.routes_bookings import router as bookings_router
from app.api.routes_chat import router as chat_router
from app.api.routes_estimate import router as estimate_router
from app.api.routes_bot import router as bot_router
from app.api.admin_safety import AdminSafetyMiddleware
from app.api.routes_checklists import router as checklists_router
from app.api.routes_health import router as health_router
from app.api.routes_client import router as client_router
from app.api.routes_payments import router as payments_router
from app.api.routes_orders import router as orders_router
from app.api.routes_time_tracking import router as time_tracking_router
from app.api.routes_ui_lang import router as ui_lang_router
from app.api.routes_worker import router as worker_router
from app.api.routes_auth import router as auth_router
from app.api.routes_iam import router as iam_router
from app.api.worker_auth import WorkerAccessMiddleware
from app.api.routes_public import router as public_router
from app.api.routes_leads import router as leads_router
from app.api.routes_billing import router as billing_router
from app.api.problem_details import (
    PROBLEM_TYPE_DOMAIN,
    PROBLEM_TYPE_RATE_LIMIT,
    PROBLEM_TYPE_SERVER,
    PROBLEM_TYPE_VALIDATION,
    problem_details,
)
from app.api.saas_auth import PasswordChangeGateMiddleware, TenantSessionMiddleware
from app.domain.errors import DomainError
from app.infra.db import get_session_factory
from app.infra.email import EmailAdapter
from app.infra.logging import clear_log_context, configure_logging, update_log_context
from app.infra.metrics import configure_metrics, metrics
from app.infra.security import RateLimiter, resolve_client_key
from app.settings import settings
from app.services import build_app_services

logger = logging.getLogger(__name__)


def _resolve_log_identity(request: Request) -> dict[str, str]:
    context: dict[str, str] = {}
    org_id = getattr(request.state, "current_org_id", None)
    user_id = getattr(request.state, "current_user_id", None)
    saas_identity = getattr(request.state, "saas_identity", None)
    worker_identity = getattr(request.state, "worker_identity", None)
    admin_identity = getattr(request.state, "admin_identity", None)

    if saas_identity:
        role = getattr(saas_identity, "role", None)
        role_value = getattr(role, "value", role)
        if role_value:
            context["role"] = str(role_value)
        resolved_org = org_id or getattr(saas_identity, "org_id", None)
        resolved_user = user_id or getattr(saas_identity, "user_id", None)
        if resolved_org:
            context["org_id"] = str(resolved_org)
        if resolved_user:
            context["user_id"] = str(resolved_user)
    elif admin_identity:
        admin_role = getattr(admin_identity, "role", None)
        admin_role_value = getattr(admin_role, "value", admin_role)
        if admin_role_value:
            context["role"] = str(admin_role_value)
        admin_org = org_id or getattr(admin_identity, "org_id", None)
        if admin_org:
            context["org_id"] = str(admin_org)
    elif worker_identity:
        context["role"] = "worker"
        worker_org = org_id or getattr(worker_identity, "org_id", None)
        if worker_org:
            context["org_id"] = str(worker_org)
        worker_user = user_id or getattr(worker_identity, "username", None)
        if worker_user:
            context["user_id"] = str(worker_user)
    else:
        if org_id:
            context["org_id"] = str(org_id)
        if user_id:
            context["user_id"] = str(user_id)
    return context


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
        request_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID")
        if not request_id:
            request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        update_log_context(request_id=request_id, method=request.method, path=request.url.path)

        response = None
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            identity_context = _resolve_log_identity(request)
            update_log_context(status_code=status_code, **identity_context)
            latency_ms = int((time.time() - start) * 1000)
            logger.info("request", extra={"latency_ms": latency_ms})
            if response is not None:
                response.headers.setdefault("X-Request-ID", request_id)
            clear_log_context()


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


class MetricsMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: FastAPI, metrics_client) -> None:
        super().__init__(app)
        self.metrics = metrics_client

    async def dispatch(self, request: Request, call_next: Callable):
        path_label = "unmatched"
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            route = request.scope.get("route")
            path_label = getattr(route, "path", path_label)
            self.metrics.record_http_5xx(request.method, path_label)
            raise
        finally:
            route = request.scope.get("route")
            path_label = getattr(route, "path", path_label)
            duration = time.perf_counter() - start
            self.metrics.record_http_latency(request.method, path_label, status_code, duration)
        if status_code >= 500:
            self.metrics.record_http_5xx(request.method, path_label)
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

    def _validate_secret(value: str | None, name: str, *, minimum: int = 32, forbidden: set[str] | None = None) -> None:
        if not value or not value.strip():
            errors.append(f"{name} is required outside dev")
            return
        if len(value.strip()) < minimum:
            errors.append(f"{name} must be at least {minimum} characters")
            return
        if forbidden and value in forbidden:
            errors.append(f"{name} must not use default or placeholder values")

    _validate_secret(
        app_settings.auth_secret_key,
        "AUTH_SECRET_KEY",
        forbidden={"dev-auth-secret"},
    )
    _validate_secret(
        app_settings.client_portal_secret,
        "CLIENT_PORTAL_SECRET",
        forbidden={"dev-client-portal-secret"},
    )
    _validate_secret(app_settings.worker_portal_secret, "WORKER_PORTAL_SECRET")
    if getattr(app_settings, "metrics_enabled", False):
        _validate_secret(app_settings.metrics_token, "METRICS_TOKEN", minimum=16)

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
        raise RuntimeError("Invalid production configuration: " + "; ".join(errors))


def create_app(app_settings) -> FastAPI:
    configure_logging()
    metrics_client = configure_metrics(app_settings.metrics_enabled)
    _validate_prod_config(app_settings)

    services = build_app_services(app_settings, metrics=metrics_client)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state_services = getattr(app.state, "services", None) or services
        app.state.services = state_services

        app.state.rate_limiter = getattr(app.state, "rate_limiter", None) or state_services.rate_limiter
        app.state.action_rate_limiter = (
            getattr(app.state, "action_rate_limiter", None) or state_services.action_rate_limiter
        )
        app.state.metrics = getattr(app.state, "metrics", None) or state_services.metrics
        app.state.storage_backend = getattr(app.state, "storage_backend", None) or state_services.storage
        app.state.app_settings = getattr(app.state, "app_settings", app_settings)
        app.state.db_session_factory = getattr(app.state, "db_session_factory", None) or get_session_factory()
        app.state.export_transport = getattr(app.state, "export_transport", None)
        app.state.export_resolver = getattr(app.state, "export_resolver", None)
        app.state.email_adapter = getattr(app.state, "email_adapter", None) or state_services.email_adapter
        app.state.stripe_client = getattr(app.state, "stripe_client", None) or state_services.stripe_client
        yield
        await app.state.rate_limiter.close()
        await app.state.action_rate_limiter.close()

    app = FastAPI(title="Cleaning Economy Bot", version="1.0.0", lifespan=lifespan)

    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    app.add_middleware(WorkerAccessMiddleware)
    app.add_middleware(AdminAuditMiddleware)
    app.add_middleware(AdminAccessMiddleware)
    app.add_middleware(AdminSafetyMiddleware, app_settings=app_settings)
    app.add_middleware(PasswordChangeGateMiddleware)
    app.add_middleware(TenantSessionMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(MetricsMiddleware, metrics_client=metrics_client)
    app.add_middleware(RateLimitMiddleware, limiter=services.rate_limiter, app_settings=app_settings)
    app.add_middleware(RequestIdMiddleware)

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
        identity_context = _resolve_log_identity(request)
        request_id = getattr(request.state, "request_id", None)
        logger.exception(
            "unhandled_exception",
            extra={"request_id": request_id, "path": request.url.path, **identity_context},
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
    app.include_router(iam_router)
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
    app.include_router(break_glass_router)
    app.include_router(admin_router)
    app.include_router(queues_router)
    app.include_router(timeline_router)
    if app_settings.metrics_enabled:
        from app.api.routes_metrics import router as metrics_router

        app.include_router(metrics_router)
    _try_include_style_guide(app)
    return app


app = create_app(settings)
