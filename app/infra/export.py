import asyncio
import logging
from typing import Any, Dict

import httpx

from app.settings import settings

logger = logging.getLogger(__name__)


async def export_lead_async(payload: Dict[str, Any]) -> None:
    """Export leads best-effort; never block lead creation."""
    if settings.export_mode == "off":
        return
    if settings.export_mode == "sheets":
        logger.warning("export_mode_sheets_not_configured")
        return
    if settings.export_mode == "webhook":
        url = settings.export_webhook_url
        if not url:
            logger.error("export_webhook_missing_url")
            return
        await _post_with_retry_async(url, payload)


async def _post_with_retry_async(url: str, payload: Dict[str, Any]) -> None:
    timeout = settings.export_webhook_timeout_seconds
    retries = settings.export_webhook_max_retries
    backoff = settings.export_webhook_backoff_seconds
    for attempt in range(1, retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=payload)
            if 200 <= response.status_code < 300:
                logger.info(
                    "export_webhook_success",
                    extra={"extra": {"lead_id": payload.get("lead_id"), "status_code": response.status_code}},
                )
                return
            logger.warning(
                "export_webhook_non_200",
                extra={
                    "extra": {
                        "lead_id": payload.get("lead_id"),
                        "status_code": response.status_code,
                        "attempt": attempt,
                    }
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "export_webhook_error",
                extra={"extra": {"lead_id": payload.get("lead_id"), "attempt": attempt, "error": str(exc)}},
            )
        if attempt < retries:
            await asyncio.sleep(backoff * attempt)
    logger.error(
        "export_webhook_failed",
        extra={"extra": {"lead_id": payload.get("lead_id"), "attempts": retries}},
    )
