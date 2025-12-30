"""Lightweight CSRF utilities for HTML endpoints."""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request, status
from fastapi.responses import Response

CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_FORM_FIELD = "csrf_token"
SAFE_METHODS: set[str] = {"GET", "HEAD", "OPTIONS", "TRACE"}


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def _cookie_token(request: Request) -> str | None:
    token = request.cookies.get(CSRF_COOKIE_NAME)
    return token or None


def get_csrf_token(request: Request) -> str:
    """Return an existing CSRF token or create a new one."""

    return _cookie_token(request) or _new_token()


def set_csrf_cookie(response: Response, token: str) -> None:
    response.set_cookie(CSRF_COOKIE_NAME, token, httponly=False, samesite="lax")


def issue_csrf_token(request: Request, response: Response, token: str | None = None) -> str:
    csrf_token = token or get_csrf_token(request)
    set_csrf_cookie(response, csrf_token)
    return csrf_token


def render_csrf_input(token: str) -> str:
    return f"<input type=\"hidden\" name=\"{CSRF_FORM_FIELD}\" value=\"{token}\" />"


async def _form_token(request: Request) -> str | None:
    try:
        form = await request.form()
    except Exception:  # noqa: BLE001
        return None
    token = form.get(CSRF_FORM_FIELD)
    return str(token) if token is not None else None


async def validate_csrf(request: Request, app_settings) -> None:
    if app_settings.testing or request.method in SAFE_METHODS:
        return

    cookie_token = _cookie_token(request)
    if not cookie_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token missing")

    header_token = request.headers.get(CSRF_HEADER_NAME)
    if header_token and secrets.compare_digest(header_token, cookie_token):
        return

    form_token = await _form_token(request)
    if form_token and secrets.compare_digest(form_token, cookie_token):
        return

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token invalid or missing")


async def require_csrf(request: Request) -> None:
    from app.settings import settings  # Local import to avoid circular dependency

    await validate_csrf(request, settings)
