import uuid
from datetime import datetime, timedelta, timezone

import anyio
import httpx

from app.domain.export_events.db_models import ExportEvent
from app.domain.outbox.db_models import OutboxEvent
from app.jobs.dlq_auto_replay import run_dlq_auto_replay
from app.settings import settings


def _configure_defaults():
    settings.dlq_auto_replay_enabled = True
    settings.dlq_auto_replay_allow_outbox_kinds = ["webhook"]
    settings.dlq_auto_replay_allow_export_modes = ["webhook"]
    settings.dlq_auto_replay_min_age_minutes = 0
    settings.dlq_auto_replay_max_per_org = 10
    settings.dlq_auto_replay_outbox_attempt_ceiling = 10
    settings.dlq_auto_replay_failure_streak_limit = 3
    settings.dlq_auto_replay_export_replay_limit = 3
    settings.dlq_auto_replay_export_cooldown_minutes = 0


def test_dlq_auto_replay_respects_org_scope(async_session_maker):
    async def _run():
        _configure_defaults()
        org_a = uuid.uuid4()
        org_b = uuid.uuid4()
        transport = httpx.MockTransport(lambda request: httpx.Response(200))
        now = datetime.now(tz=timezone.utc)
        async with async_session_maker() as session:
            dead_a = OutboxEvent(
                org_id=org_a,
                kind="webhook",
                payload_json={"url": "https://example.com/hook", "payload": {}},
                dedupe_key="webhook:a",
                status="dead",
                attempts=settings.outbox_max_attempts,
                created_at=now,
            )
            dead_b = OutboxEvent(
                org_id=org_b,
                kind="webhook",
                payload_json={"url": "https://example.com/hook", "payload": {}},
                dedupe_key="webhook:b",
                status="dead",
                attempts=settings.outbox_max_attempts,
                created_at=now,
            )
            export_a = ExportEvent(
                org_id=org_a,
                mode="webhook",
                payload={"lead_id": "123", "org_id": str(org_a)},
                attempts=1,
                last_error_code="status_500",
                target_url="https://example.com/export",
                created_at=now,
            )
            export_b = ExportEvent(
                org_id=org_b,
                mode="webhook",
                payload={"lead_id": "456", "org_id": str(org_b)},
                attempts=1,
                last_error_code="status_500",
                target_url="https://example.com/export",
                created_at=now,
            )
            session.add_all([dead_a, dead_b, export_a, export_b])
            await session.commit()

            await run_dlq_auto_replay(
                session,
                adapter=None,
                org_id=org_a,
                export_transport=transport,
                export_resolver=lambda host: ["93.184.216.34"],
            )

            await session.refresh(dead_a)
            await session.refresh(dead_b)
            await session.refresh(export_a)
            await session.refresh(export_b)
            assert dead_a.status == "sent"
            assert dead_b.status == "dead"
            assert export_a.last_error_code is None
            assert export_b.last_error_code is not None

    anyio.run(_run)


def test_dlq_auto_replay_skips_ineligible(async_session_maker):
    async def _run():
        _configure_defaults()
        settings.dlq_auto_replay_allow_outbox_kinds = ["webhook"]
        settings.dlq_auto_replay_outbox_attempt_ceiling = settings.outbox_max_attempts
        org_id = uuid.uuid4()
        now = datetime.now(tz=timezone.utc)
        async with async_session_maker() as session:
            too_fresh = OutboxEvent(
                org_id=org_id,
                kind="webhook",
                payload_json={"url": "https://example.com/hook", "payload": {}},
                dedupe_key="webhook:fresh",
                status="dead",
                attempts=settings.outbox_max_attempts,
                created_at=now,
            )
            wrong_kind = OutboxEvent(
                org_id=org_id,
                kind="email",
                payload_json={"recipient": "test@example.com", "subject": "Hi", "body": "Hello"},
                dedupe_key="email:dead",
                status="dead",
                attempts=settings.outbox_max_attempts,
                created_at=now - timedelta(minutes=120),
            )
            recent_export = ExportEvent(
                org_id=org_id,
                mode="webhook",
                payload={"lead_id": "123"},
                attempts=1,
                last_error_code="status_500",
                target_url="https://example.com/export",
                created_at=now,
            )
            session.add_all([too_fresh, wrong_kind, recent_export])
            await session.commit()

            settings.dlq_auto_replay_min_age_minutes = 90
            await run_dlq_auto_replay(
                session,
                adapter=None,
                org_id=org_id,
                export_transport=httpx.MockTransport(lambda request: httpx.Response(200)),
                export_resolver=lambda host: ["127.0.0.1"],
            )

            await session.refresh(too_fresh)
            await session.refresh(wrong_kind)
            await session.refresh(recent_export)
            assert too_fresh.status == "dead"
            assert wrong_kind.status == "dead"
            assert recent_export.last_error_code is not None

    anyio.run(_run)


def test_dlq_auto_replay_idempotent(async_session_maker):
    async def _run():
        _configure_defaults()
        org_id = uuid.uuid4()
        transport = httpx.MockTransport(lambda request: httpx.Response(200))
        now = datetime.now(tz=timezone.utc)
        async with async_session_maker() as session:
            dead_event = OutboxEvent(
                org_id=org_id,
                kind="webhook",
                payload_json={"url": "https://example.com/hook", "payload": {}},
                dedupe_key="webhook:idem",
                status="dead",
                attempts=settings.outbox_max_attempts,
                created_at=now,
            )
            session.add(dead_event)
            await session.commit()

            await run_dlq_auto_replay(
                session,
                adapter=None,
                org_id=org_id,
                export_transport=transport,
                export_resolver=lambda host: ["127.0.0.1"],
            )
            await session.refresh(dead_event)
            first_attempts = dead_event.attempts
            assert dead_event.status == "sent"

            await run_dlq_auto_replay(
                session,
                adapter=None,
                org_id=org_id,
                export_transport=transport,
                export_resolver=lambda host: ["127.0.0.1"],
            )
            await session.refresh(dead_event)
            assert dead_event.status == "sent"
            assert dead_event.attempts == first_attempts

    anyio.run(_run)
