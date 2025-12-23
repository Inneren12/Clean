import logging
import time
import uuid
from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.routes_chat import router as chat_router
from app.api.routes_estimate import router as estimate_router
from app.api.routes_health import router as health_router
from app.api.routes_leads import router as leads_router
from app.domain.errors import DomainError
from app.infra.logging import configure_logging
from app.infra.security import RateLimiter
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
) -> JSONResponse:
    content = {
        "type": type_,
        "title": title,
        "status": status,
        "detail": detail,
        "request_id": getattr(request.state, "request_id", None),
        "errors": errors or [],
    }
    return JSONResponse(status_code=status, content=content)


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
                "extra": {
                    "request_id": request.state.request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "latency_ms": latency_ms,
                }
            },
        )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: FastAPI, limiter: RateLimiter) -> None:
        super().__init__(app)
        self.limiter = limiter

    async def dispatch(self, request: Request, call_next: Callable):
        client = request.client.host if request.client else "unknown"
        if not self.limiter.allow(client):
            return problem_details(
                request=request,
                status=429,
                title="Too Many Requests",
                detail="Rate limit exceeded",
                type_=PROBLEM_TYPE_RATE_LIMIT,
            )
        return await call_next(request)


configure_logging()
app = FastAPI(title="Cleaning Economy Bot", version="1.0.0")

rate_limiter = RateLimiter(settings.rate_limit_per_minute)
app.state.rate_limiter = rate_limiter

app.add_middleware(RequestIdMiddleware)
app.add_middleware(LoggingMiddleware)
app.add_middleware(RateLimitMiddleware, limiter=rate_limiter)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins or ["*"],
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
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception(
        "unhandled_exception",
        extra={"extra": {"request_id": getattr(request.state, "request_id", None), "path": request.url.path}},
    )
    return problem_details(
        request=request,
        status=500,
        title="Internal Server Error",
        detail="Unexpected error",
        type_=PROBLEM_TYPE_SERVER,
    )


app.include_router(health_router)
app.include_router(estimate_router)
app.include_router(chat_router)
app.include_router(leads_router)
