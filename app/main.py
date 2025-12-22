import logging
import time
import uuid
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.routes_chat import router as chat_router
from app.api.routes_estimate import router as estimate_router
from app.api.routes_health import router as health_router
from app.api.routes_leads import router as leads_router
from app.dependencies import get_pricing_config
from app.infra.logging import configure_logging
from app.infra.security import RateLimiter
from app.settings import settings


class ProblemDetails(JSONResponse):
    def __init__(self, status: int, title: str, detail: str, type_: str = "about:blank") -> None:
        content = {
            "type": type_,
            "title": title,
            "status": status,
            "detail": detail,
        }
        super().__init__(status_code=status, content=content)


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
            return ProblemDetails(
                status=429,
                title="Too Many Requests",
                detail="Rate limit exceeded",
            )
        return await call_next(request)


configure_logging()
app = FastAPI(title="Cleaning Economy Bot", version="1.0.0")

app.add_middleware(RequestIdMiddleware)
app.add_middleware(LoggingMiddleware)
app.add_middleware(RateLimitMiddleware, limiter=RateLimiter(settings.rate_limit_per_minute))

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins or ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return ProblemDetails(
        status=422,
        title="Validation Error",
        detail=str(exc),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return ProblemDetails(
        status=500,
        title="Internal Server Error",
        detail="Unexpected error",
    )


app.include_router(health_router)
app.include_router(estimate_router)
app.include_router(chat_router)
app.include_router(leads_router)
