import asyncio
import logging
import socket
from ipaddress import ip_address
import uuid
from typing import Any, Callable, Dict, Iterable
from urllib.parse import urlparse

import anyio
import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.export_events.db_models import ExportEvent
from app.domain.outbox.service import enqueue_outbox_event
from app.infra.db import get_session_factory
from app.settings import settings

logger = logging.getLogger(__name__)

Resolver = Callable[[str], Iterable[str]]


async def export_lead_async(
    payload: Dict[str, Any],
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: Resolver | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
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
        is_valid, reason = await validate_webhook_url(url, resolver=resolver)
        if not is_valid:
            logger.error(
                "export_webhook_invalid_url",
                extra={"extra": {"lead_id": payload.get("lead_id"), "reason": reason}},
            )
            return
        await _enqueue_export_outbox(session_factory, payload, url)


async def send_export_with_retry(
    url: str,
    payload: Dict[str, Any],
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[bool, int, str]:
    timeout = settings.export_webhook_timeout_seconds
    retries = settings.export_webhook_max_retries
    backoff = settings.export_webhook_backoff_seconds
    last_error_code = "unknown"
    for attempt in range(1, retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
                response = await client.post(url, json=payload)
            if 200 <= response.status_code < 300:
                logger.info(
                    "export_webhook_success",
                    extra={"extra": {"lead_id": payload.get("lead_id"), "status_code": response.status_code}},
                )
                return True, attempt, ""
            last_error_code = f"status_{response.status_code}"
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
            last_error_code = type(exc).__name__
            logger.warning(
                "export_webhook_error",
                extra={"extra": {"lead_id": payload.get("lead_id"), "attempt": attempt, "error": str(exc)}},
            )
        if attempt < retries:
            await asyncio.sleep(backoff * attempt)
    logger.error(
        "export_webhook_failed",
        extra={
            "extra": {
                "lead_id": payload.get("lead_id"),
                "attempts": retries,
                "last_error_code": last_error_code,
            }
        },
    )
    return False, retries, last_error_code


async def _enqueue_export_outbox(
    session_factory: async_sessionmaker[AsyncSession] | None,
    payload: dict,
    url: str,
) -> None:
    try:
        factory = session_factory or get_session_factory()
    except Exception:  # noqa: BLE001
        logger.warning("export_outbox_session_unavailable")
        return

    try:
        async with factory() as session:
            org_id = (payload or {}).get("org_id") or settings.default_org_id
            try:
                org_uuid = uuid.UUID(str(org_id))
            except Exception:
                org_uuid = settings.default_org_id
            dedupe_key = f"export:{org_uuid}:{url}:{payload.get('lead_id', 'unknown')}"
            await enqueue_outbox_event(
                session,
                org_id=org_uuid,
                kind="export",
                payload={"payload": payload, "target_url": url},
                dedupe_key=dedupe_key,
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "export_outbox_enqueue_failed",
            extra={"extra": {"lead_id": (payload or {}).get("lead_id"), "error": str(exc)}},
        )


async def _record_export_dead_letter(
    session_factory: async_sessionmaker[AsyncSession] | None,
    payload: dict | None,
    url: str,
    attempts: int,
    last_error_code: str,
    mode: str,
) -> None:
    try:
        factory = session_factory or get_session_factory()
    except Exception:  # noqa: BLE001
        logger.warning("export_dead_letter_session_unavailable")
        return

    try:
        host = urlparse(url).hostname
    except Exception:  # noqa: BLE001
        host = None

    try:
        async with factory() as session:
            org_id = (payload or {}).get("org_id") or settings.default_org_id
            try:
                org_uuid = uuid.UUID(str(org_id))
            except Exception:
                org_uuid = settings.default_org_id
            event = ExportEvent(
                lead_id=(payload or {}).get("lead_id"),
                mode=mode,
                payload=payload,
                target_url_host=host,
                target_url=url,
                attempts=attempts,
                last_error_code=last_error_code,
                org_id=org_uuid,
            )
            session.add(event)
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "export_dead_letter_record_failed",
            extra={"extra": {"lead_id": (payload or {}).get("lead_id"), "error": str(exc)}},
        )


async def validate_webhook_url(url: str, resolver: Resolver | None = None) -> tuple[bool, str]:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return False, "invalid_url"
    scheme = parsed.scheme.lower()
    if scheme == "http" and not settings.export_webhook_allow_http:
        return False, "http_not_allowed"
    if scheme not in {"http", "https"}:
        return False, "unsupported_scheme"
    host = parsed.hostname
    if not host:
        return False, "missing_host"
    if settings.export_webhook_allowed_hosts:
        allowed_hosts = {entry.lower() for entry in settings.export_webhook_allowed_hosts}
        if host.lower() not in allowed_hosts:
            return False, "host_not_allowlisted"
    elif settings.app_env == "prod":
        return False, "allowlist_required"
    if not settings.export_webhook_block_private_ips:
        return True, "ok"
    if host.lower() == "localhost":
        return False, "private_ip_blocked"
    if resolver is None:
        ips = await _resolve_host_ips_async(host)
    else:
        ips = list(resolver(host))
    if not ips:
        return False, "dns_lookup_failed"
    for ip in ips:
        if _is_private_ip(ip):
            return False, "private_ip_blocked"
    return True, "ok"


async def _resolve_host_ips_async(host: str) -> Iterable[str]:
    try:
        infos = await anyio.to_thread.run_sync(socket.getaddrinfo, host, None)
    except OSError:
        return []
    addresses = []
    for info in infos:
        address = info[4][0]
        if address not in addresses:
            addresses.append(address)
    return addresses


def _is_private_ip(host: str) -> bool:
    try:
        ip = ip_address(host)
    except ValueError:
        return True
    return ip.is_private or ip.is_loopback or ip.is_link_local
