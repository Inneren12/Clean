import uuid

import anyio
import httpx
from sqlalchemy import func, select

from app.domain.outbox.db_models import OutboxEvent
from app.domain.outbox.service import OutboxAdapters, enqueue_outbox_event, process_outbox, replay_outbox_event
from app.settings import settings


def test_outbox_enqueue_idempotent(async_session_maker):
    async def _run():
        dedupe_key = "idempotent:test"
        async with async_session_maker() as session:
            await enqueue_outbox_event(
                session,
                org_id=settings.default_org_id,
                kind="webhook",
                payload={"url": "https://example.com", "payload": {}},
                dedupe_key=dedupe_key,
            )
            await session.commit()

        async with async_session_maker() as session:
            await enqueue_outbox_event(
                session,
                org_id=settings.default_org_id,
                kind="webhook",
                payload={"url": "https://example.com", "payload": {}},
                dedupe_key=dedupe_key,
            )
            await session.commit()
            count = await session.scalar(select(func.count()).where(OutboxEvent.dedupe_key == dedupe_key))
            assert count == 1

    anyio.run(_run)


def test_outbox_processes_and_marks_sent(async_session_maker):
    async def _run():
        transport = httpx.MockTransport(lambda request: httpx.Response(200))
        async with async_session_maker() as session:
            event_ref = await enqueue_outbox_event(
                session,
                org_id=settings.default_org_id,
                kind="webhook",
                payload={"url": "https://example.com/hook", "payload": {"ok": True}},
                dedupe_key=f"webhook:{uuid.uuid4()}",
            )
            await session.commit()
            event_id = event_ref if isinstance(event_ref, str) else event_ref.event_id
            event = await session.get(OutboxEvent, event_id)
            adapters = OutboxAdapters(export_transport=transport)
            result = await process_outbox(session, adapters, limit=10)
            await session.refresh(event)
            assert result["sent"] == 1
            assert event.status == "sent"
            assert event.last_error is None

    anyio.run(_run)


def test_outbox_dlq_and_replay(async_session_maker, monkeypatch):
    async def _run():
        monkeypatch.setattr(settings, "outbox_max_attempts", 1)
        failing_transport = httpx.MockTransport(lambda request: httpx.Response(500))
        async with async_session_maker() as session:
            event_ref = await enqueue_outbox_event(
                session,
                org_id=settings.default_org_id,
                kind="webhook",
                payload={"url": "https://example.com/fail", "payload": {}},
                dedupe_key=f"webhook:{uuid.uuid4()}",
            )
            await session.commit()
            event_id = event_ref if isinstance(event_ref, str) else event_ref.event_id
            event = await session.get(OutboxEvent, event_id)
            adapters = OutboxAdapters(export_transport=failing_transport)
            await process_outbox(session, adapters, limit=10)
            await session.refresh(event)
            assert event.status == "dead"
            assert event.last_error is not None

            await replay_outbox_event(session, event)
            assert event.status == "pending"
            assert event.attempts == 0

            adapters = OutboxAdapters(export_transport=httpx.MockTransport(lambda request: httpx.Response(200)))
            await process_outbox(session, adapters, limit=10)
            await session.refresh(event)
            assert event.status == "sent"

    anyio.run(_run)
