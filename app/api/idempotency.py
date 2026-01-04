from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
import uuid

from fastapi import Request, status
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.problem_details import (
    PROBLEM_TYPE_DOMAIN,
    PROBLEM_TYPE_RATE_LIMIT,
    PROBLEM_TYPE_VALIDATION,
    problem_details,
)
from app.domain.admin_idempotency import AdminIdempotency
from app.infra.security import RateLimiter


def _build_response(record: AdminIdempotency) -> Response:
    if record.response_body_json is None:
        response: Response = Response(status_code=record.response_status)
    else:
        response = JSONResponse(content=record.response_body_json, status_code=record.response_status)
    response.headers["Idempotency-Key"] = record.key
    return response


async def _normalized_body(request: Request) -> str:
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        raw_body = await request.body()
        return raw_body.decode() if raw_body else ""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _request_fingerprint(request: Request, body: str) -> str:
    payload = f"{request.method}:{request.url.path}:{body}".encode()
    return hashlib.sha256(payload).hexdigest()


@dataclass
class IdempotencyContext:
    key: str
    org_id: uuid.UUID
    endpoint_id: str
    request_hash: str
    existing_record: AdminIdempotency | None = None
    existing_response: Response | None = None

    async def save_response(
        self, session: AsyncSession, *, status_code: int, body: dict | list | None
    ) -> None:
        if self.existing_record is not None:
            return

        record = AdminIdempotency(
            org_id=self.org_id,
            key=self.key,
            endpoint=self.endpoint_id,
            request_hash=self.request_hash,
            response_status=status_code,
            response_body_json=body if isinstance(body, (dict, list)) else None,
        )
        session.add(record)
        self.existing_record = record
        self.existing_response = _build_response(record)


async def require_idempotency(
    request: Request, session: AsyncSession, org_id: uuid.UUID, endpoint_id: str
) -> IdempotencyContext | Response:
    key = request.headers.get("Idempotency-Key")
    if not key:
        return problem_details(
            request=request,
            status=status.HTTP_400_BAD_REQUEST,
            title="Idempotency key required",
            detail="Idempotency-Key header is required for this endpoint",
            type_=PROBLEM_TYPE_VALIDATION,
        )

    normalized_body = await _normalized_body(request)
    fingerprint = _request_fingerprint(request, normalized_body)

    stmt = select(AdminIdempotency).where(
        AdminIdempotency.org_id == org_id,
        AdminIdempotency.key == key,
        AdminIdempotency.endpoint == endpoint_id,
    )
    result = await session.execute(stmt)
    record = result.scalar_one_or_none()
    if record:
        if record.request_hash != fingerprint:
            return problem_details(
                request=request,
                status=status.HTTP_409_CONFLICT,
                title="Idempotency conflict",
                detail="Idempotency-Key has already been used with a different payload",
                type_=PROBLEM_TYPE_DOMAIN,
            )
        return _build_response(record)

    return IdempotencyContext(
        key=key,
        org_id=org_id,
        endpoint_id=endpoint_id,
        request_hash=fingerprint,
    )


async def enforce_org_action_rate_limit(
    request: Request, org_id: uuid.UUID, action: str, limiter: RateLimiter | None = None
) -> Response | None:
    limiter = limiter or _resolve_rate_limiter(request)
    if limiter is None:
        return None

    key = f"org:{org_id}:action:{action}"
    allowed = await limiter.allow(key)
    if allowed:
        return None

    return problem_details(
        request=request,
        status=status.HTTP_429_TOO_MANY_REQUESTS,
        title="Too Many Requests",
        detail="Rate limit exceeded for this action",
        type_=PROBLEM_TYPE_RATE_LIMIT,
    )


def _resolve_rate_limiter(request: Request) -> RateLimiter | None:
    limiter = getattr(request.app.state, "action_rate_limiter", None)
    if limiter:
        return limiter
    services = getattr(request.app.state, "services", None)
    return getattr(services, "action_rate_limiter", None) or getattr(request.app.state, "rate_limiter", None)
