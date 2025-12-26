from __future__ import annotations

import logging
from typing import Any

import httpx

from app.settings import settings

logger = logging.getLogger(__name__)
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


async def verify_turnstile(
    token: str | None,
    remote_ip: str | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bool:
    if settings.captcha_mode == "off":
        return True

    if settings.captcha_mode != "turnstile":
        logger.warning("captcha_mode_unknown", extra={"extra": {"mode": settings.captcha_mode}})
        return False

    if not token:
        return False
    if not settings.turnstile_secret_key:
        logger.warning("turnstile_secret_missing")
        return False

    try:
        async with httpx.AsyncClient(timeout=5, transport=transport) as client:
            response = await client.post(
                TURNSTILE_VERIFY_URL,
                data={"secret": settings.turnstile_secret_key, "response": token, "remoteip": remote_ip},
            )
        payload: dict[str, Any] = response.json()
    except Exception:  # noqa: BLE001
        logger.warning("turnstile_verify_failed")
        return False

    return bool(payload.get("success"))
