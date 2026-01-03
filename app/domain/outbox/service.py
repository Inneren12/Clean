from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable
from urllib.parse import urlparse

import httpx
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.export_events.db_models import ExportEvent
from app.domain.outbox.db_models import OutboxEvent
from app.infra.email import EmailAdapter
from app.settings import settings

PENDING_STATUSES = {"pending", "retry"}


class OutboxAdapters:
    def __init__(
        self,
        *,
        email_adapter: EmailAdapter | None = None,
        export_transport: httpx.AsyncBaseTransport | None = None,
        export_resolver: Callable[[str], Iterable[str]] | None = None,
    ) -> None:
        self.email_adapter = email_adapter
        self.export_transport = export_transport
        self.export_resolver = export_resolver


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _backoff_delay(attempt: int) -> timedelta:
    delay = settings.outbox_base_backoff_seconds * max(1, 2 ** max(0, attempt - 1))
    return timedelta(seconds=delay)


def _next_attempt(attempt: int) -> datetime:
    return _now() + _backoff_delay(attempt)


async def enqueue_outbox_event(
    session: AsyncSession,
    *,
    org_id,
    kind: str,
    payload: dict,
    dedupe_key: str,
) -> OutboxEvent:
    values = {
        "org_id": org_id,
        "kind": kind,
        "payload_json": payload,
        "dedupe_key": dedupe_key,
        "status": "pending",
        "attempts": 0,
        "next_attempt_at": _now(),
        "last_error": None,
    }
    bind = session.get_bind()
    dialect = bind.dialect.name if bind else ""
    if dialect == "postgresql":
        stmt = pg_insert(OutboxEvent).values(**values).on_conflict_do_nothing(
            constraint="ix_outbox_org_dedupe"
        )
        result = await session.execute(stmt.returning(OutboxEvent))
        created = result.scalar_one_or_none()
        if created is not None:
            return created
    else:
        try:
            stmt = OutboxEvent.__table__.insert().values(**values)
            result = await session.execute(stmt.returning(OutboxEvent))
            created = result.scalar_one_or_none()
            if created is not None:
                return created
        except Exception:
            await session.rollback()
    existing = await session.scalar(
        select(OutboxEvent).where(OutboxEvent.org_id == org_id, OutboxEvent.dedupe_key == dedupe_key)
    )
    if existing:
        return existing
    # Should not happen, but ensure we have a row
    stmt = OutboxEvent.__table__.insert().values(**values)
    result = await session.execute(stmt.returning(OutboxEvent))
    return result.scalar_one()


async def _deliver_email(adapters: OutboxAdapters, payload: dict) -> tuple[bool, str | None]:
    from app.domain.notifications.email_service import _try_send_email  # lazy import

    recipient = payload.get("recipient")
    subject = payload.get("subject")
    body = payload.get("body")
    headers = payload.get("headers") or None
    context = payload.get("context") or {}
    if not recipient or not subject or not body:
        return False, "missing_payload"
    delivered = await _try_send_email(
        adapters.email_adapter,
        recipient,
        subject,
        body,
        headers=headers,
        context=context,
    )
    return delivered, None if delivered else "send_failed"


async def _deliver_webhook(adapters: OutboxAdapters, payload: dict) -> tuple[bool, str | None]:
    url = payload.get("url")
    body = payload.get("payload") or {}
    if not url:
        return False, "missing_url"
    try:
        async with httpx.AsyncClient(
            timeout=settings.export_webhook_timeout_seconds, transport=adapters.export_transport
        ) as client:
            response = await client.post(url, json=body)
        if 200 <= response.status_code < 300:
            return True, None
        return False, f"status_{response.status_code}"
    except Exception as exc:  # noqa: BLE001
        return False, type(exc).__name__


async def _deliver_export(adapters: OutboxAdapters, payload: dict) -> tuple[bool, str | None]:
    from app.infra.export import validate_webhook_url

    url = payload.get("target_url") or settings.export_webhook_url
    body = payload.get("payload") or {}
    if not url:
        return False, "missing_url"
    resolver = adapters.export_resolver
    is_valid, reason = await validate_webhook_url(url, resolver=resolver)
    if not is_valid:
        return False, reason
    ok, error = await _deliver_webhook(adapters, {"url": url, "payload": body})
    return ok, error


async def _deliver_event(event: OutboxEvent, adapters: OutboxAdapters) -> tuple[bool, str | None]:
    if event.kind == "email":
        return await _deliver_email(adapters, event.payload_json)
    if event.kind == "webhook":
        return await _deliver_webhook(adapters, event.payload_json)
    if event.kind == "export":
        return await _deliver_export(adapters, event.payload_json)
    return False, "unknown_kind"


def _build_export_event(event: OutboxEvent) -> ExportEvent:
    payload = event.payload_json or {}
    export_payload = payload.get("payload") or {}
    target_url = payload.get("target_url")
    host = urlparse(target_url).hostname if target_url else None
    return ExportEvent(
        lead_id=export_payload.get("lead_id"),
        mode="webhook",
        payload=export_payload,
        target_url=target_url,
        target_url_host=host,
        attempts=event.attempts,
        last_error_code=event.last_error,
        org_id=event.org_id,
    )


async def process_outbox(session: AsyncSession, adapters: OutboxAdapters, *, limit: int = 50) -> dict[str, int]:
    now = _now()
    result = await session.execute(
        select(OutboxEvent)
        .where(OutboxEvent.status.in_(PENDING_STATUSES), OutboxEvent.next_attempt_at <= now)
        .order_by(OutboxEvent.created_at)
        .limit(limit)
    )
    events = result.scalars().all()
    sent = 0
    dead = 0
    for event in events:
        attempts = (event.attempts or 0) + 1
        event.attempts = attempts
        delivered, error = await _deliver_event(event, adapters)
        if delivered:
            event.status = "sent"
            event.next_attempt_at = None
            event.last_error = None
            sent += 1
        else:
            event.last_error = error or "failed"
            if attempts >= settings.outbox_max_attempts:
                event.status = "dead"
                event.next_attempt_at = None
                dead += 1
                if event.kind == "export":
                    session.add(_build_export_event(event))
            else:
                event.status = "retry"
                event.next_attempt_at = _next_attempt(attempts)
    if events:
        await session.commit()
    return {"sent": sent, "dead": dead, "pending": len(events)}


async def replay_outbox_event(session: AsyncSession, event: OutboxEvent) -> None:
    event.status = "pending"
    event.attempts = 0
    event.next_attempt_at = _now()
    event.last_error = None
    await session.commit()
