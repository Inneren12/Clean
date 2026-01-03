import uuid
from http import HTTPStatus
from typing import Any

from fastapi import Request, status
from fastapi.responses import JSONResponse

PROBLEM_TYPE_VALIDATION = "https://example.com/problems/validation-error"
PROBLEM_TYPE_DOMAIN = "https://example.com/problems/domain-error"
PROBLEM_TYPE_RATE_LIMIT = "https://example.com/problems/rate-limit"
PROBLEM_TYPE_SERVER = "https://example.com/problems/server-error"


def _resolve_request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID")
    if not request_id:
        request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    return request_id


def _resolve_title(status_code: int, fallback: str | None) -> str:
    if fallback:
        return fallback
    try:
        return HTTPStatus(status_code).phrase
    except Exception:  # noqa: BLE001
        return "Error"


def _resolve_type(status_code: int, type_override: str | None) -> str:
    if type_override:
        return type_override
    if status_code == status.HTTP_422_UNPROCESSABLE_ENTITY:
        return PROBLEM_TYPE_VALIDATION
    if status_code == status.HTTP_429_TOO_MANY_REQUESTS:
        return PROBLEM_TYPE_RATE_LIMIT
    if status_code >= 500:
        return PROBLEM_TYPE_SERVER
    return PROBLEM_TYPE_DOMAIN


def problem_details(
    request: Request,
    *,
    status: int,
    title: str | None,
    detail: str,
    errors: list[dict[str, Any]] | None = None,
    type_: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = _resolve_request_id(request)
    content = {
        "type": _resolve_type(status, type_),
        "title": _resolve_title(status, title),
        "status": status,
        "detail": detail,
        "request_id": request_id,
        "errors": errors or [],
    }
    response = JSONResponse(
        status_code=status,
        content=content,
        headers=headers,
        media_type="application/problem+json",
    )
    response.headers.setdefault("X-Request-ID", request_id)
    return response
