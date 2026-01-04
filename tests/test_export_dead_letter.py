import anyio
import anyio
from httpx import MockTransport, Request, Response
from sqlalchemy import select
from datetime import datetime, timezone
import uuid

from app.domain.export_events.db_models import ExportEvent
from app.domain.outbox.service import OutboxAdapters, process_outbox
from app.infra.export import export_lead_async
from app.main import app
from app.settings import settings


def test_export_dead_letter_recorded_on_failure(async_session_maker):
    original_mode = settings.export_mode
    original_url = settings.export_webhook_url
    original_retries = settings.export_webhook_max_retries
    original_outbox_attempts = settings.outbox_max_attempts
    original_outbox_attempts = settings.outbox_max_attempts
    original_allow_http = settings.export_webhook_allow_http
    original_block_private = settings.export_webhook_block_private_ips
    original_env = settings.app_env

    settings.export_mode = "webhook"
    settings.export_webhook_url = "http://example.com/webhook"
    settings.export_webhook_max_retries = 2
    settings.outbox_max_attempts = 1
    settings.outbox_max_attempts = 1
    settings.export_webhook_allow_http = True
    settings.export_webhook_block_private_ips = False
    settings.app_env = "dev"

    transport = MockTransport(lambda request: Response(500, request=Request("POST", request.url)))
    payload = {"lead_id": "lead-dead-letter"}

    try:
        anyio.run(
            export_lead_async,
            payload,
            transport,
            None,
            async_session_maker,
        )

        async def _process():
            async with async_session_maker() as session:
                adapters = OutboxAdapters(export_transport=transport)
                await process_outbox(session, adapters)

        anyio.run(_process)

        async def _process():
            async with async_session_maker() as session:
                adapters = OutboxAdapters(export_transport=transport)
                await process_outbox(session, adapters)

        anyio.run(_process)

        async def fetch_events():
            async with async_session_maker() as session:
                result = await session.execute(select(ExportEvent))
                return result.scalars().all()

        events = anyio.run(fetch_events)
        assert len(events) == 1
        event = events[0]
        assert event.lead_id == "lead-dead-letter"
        assert event.attempts == settings.outbox_max_attempts
        assert event.last_error_code == "status_500"
        assert event.target_url_host == "example.com"
        assert event.target_url == settings.export_webhook_url
        assert event.payload and event.payload.get("lead_id") == "lead-dead-letter"
        assert event.replay_count == 0
    finally:
        settings.export_mode = original_mode
        settings.export_webhook_url = original_url
        settings.export_webhook_max_retries = original_retries
        settings.export_webhook_allow_http = original_allow_http
        settings.export_webhook_block_private_ips = original_block_private
        settings.app_env = original_env
        settings.outbox_max_attempts = original_outbox_attempts


def test_export_dead_letter_paginates(client, async_session_maker):
    original_viewer_username = settings.viewer_basic_username
    original_viewer_password = settings.viewer_basic_password

    settings.viewer_basic_username = "viewer"
    settings.viewer_basic_password = "pw"

    async def seed_events():
        async with async_session_maker() as session:
            now = datetime.now(timezone.utc)
            session.add_all(
                [
                    ExportEvent(
                        event_id=str(uuid.uuid4()),
                        org_id=settings.default_org_id,
                        lead_id=f"lead-{idx}",
                        mode="webhook",
                        target_url="https://example.com/webhook",
                        target_url_host="example.com",
                        payload={"seq": idx},
                        attempts=idx,
                        last_error_code="status_500",
                        created_at=now.replace(microsecond=idx),
                    )
                    for idx in range(3)
                ]
            )
            await session.commit()

    try:
        anyio.run(seed_events)

        first_page = client.get(
            "/v1/admin/export-dead-letter?limit=2&offset=0",
            auth=("viewer", "pw"),
        )
        assert first_page.status_code == 200
        payload_one = first_page.json()
        assert payload_one["total"] >= 3
        assert len(payload_one["items"]) == 2

        second_page = client.get(
            "/v1/admin/export-dead-letter?limit=2&offset=2",
            auth=("viewer", "pw"),
        )
        assert second_page.status_code == 200
        payload_two = second_page.json()
        assert payload_two["total"] >= 3
        assert len(payload_two["items"]) == 1

        seen_leads = {item["lead_id"] for item in payload_one["items"] + payload_two["items"]}
        assert seen_leads.issuperset({"lead-0", "lead-1", "lead-2"})
    finally:
        settings.viewer_basic_username = original_viewer_username
        settings.viewer_basic_password = original_viewer_password


def test_export_dead_letter_endpoint_allows_dispatcher(client, async_session_maker):
    original_mode = settings.export_mode
    original_url = settings.export_webhook_url
    original_retries = settings.export_webhook_max_retries
    original_allow_http = settings.export_webhook_allow_http
    original_block_private = settings.export_webhook_block_private_ips
    original_allowed_hosts = settings.export_webhook_allowed_hosts
    original_dispatcher_username = settings.dispatcher_basic_username
    original_dispatcher_password = settings.dispatcher_basic_password
    original_outbox_attempts = settings.outbox_max_attempts

    settings.export_mode = "webhook"
    settings.export_webhook_url = "https://example.com/webhook"
    settings.export_webhook_max_retries = 2
    settings.export_webhook_allow_http = False
    settings.export_webhook_block_private_ips = False
    settings.export_webhook_allowed_hosts = ["example.com"]
    settings.dispatcher_basic_username = "dispatcher"
    settings.dispatcher_basic_password = "password"
    settings.outbox_max_attempts = 1

    transport = MockTransport(lambda request: Response(500, request=Request("POST", request.url)))
    payload = {"lead_id": "lead-dead-letter-api"}

    try:
        anyio.run(
            export_lead_async,
            payload,
            transport,
            None,
            async_session_maker,
        )

        async def _process():
            async with async_session_maker() as session:
                adapters = OutboxAdapters(export_transport=transport)
                await process_outbox(session, adapters)

        anyio.run(_process)

        response = client.get(
            "/v1/admin/export-dead-letter",
            auth=("dispatcher", "password"),
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] >= 1
        events = payload["items"]
        event = next(
            (evt for evt in events if evt["lead_id"] == "lead-dead-letter-api"),
            None,
        )
        assert event is not None
        assert event["lead_id"] == "lead-dead-letter-api"
        assert event["mode"] == "webhook"
        assert event["target_url_host"] == "example.com"
        assert event["attempts"] == settings.outbox_max_attempts
        assert event["last_error_code"]
        assert event["event_id"]
        assert event["created_at"]
    finally:
        settings.export_mode = original_mode
        settings.export_webhook_url = original_url
        settings.export_webhook_max_retries = original_retries
        settings.export_webhook_allow_http = original_allow_http
        settings.export_webhook_block_private_ips = original_block_private
        settings.export_webhook_allowed_hosts = original_allowed_hosts
        settings.dispatcher_basic_username = original_dispatcher_username
        settings.dispatcher_basic_password = original_dispatcher_password
        settings.outbox_max_attempts = original_outbox_attempts


def test_export_dead_letter_replay(client, async_session_maker):
    original_mode = settings.export_mode
    original_url = settings.export_webhook_url
    original_retries = settings.export_webhook_max_retries
    original_allow_http = settings.export_webhook_allow_http
    original_block_private = settings.export_webhook_block_private_ips
    original_admin_username = settings.admin_basic_username
    original_admin_password = settings.admin_basic_password
    original_transport = getattr(app.state, "export_transport", None)
    original_outbox_attempts = settings.outbox_max_attempts

    settings.export_mode = "webhook"
    settings.export_webhook_url = "http://example.com/webhook"
    settings.export_webhook_max_retries = 2
    settings.outbox_max_attempts = 1
    settings.export_webhook_allow_http = True
    settings.export_webhook_block_private_ips = False
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "password"

    fail_transport = MockTransport(lambda request: Response(500, request=Request("POST", request.url)))
    payload = {"lead_id": "lead-dead-letter-replay", "org_id": str(settings.default_org_id)}

    try:
        anyio.run(
            export_lead_async,
            payload,
            fail_transport,
            None,
            async_session_maker,
        )

        async def _process():
            async with async_session_maker() as session:
                adapters = OutboxAdapters(export_transport=fail_transport)
                await process_outbox(session, adapters)

        anyio.run(_process)

        async def fetch_event():
            async with async_session_maker() as session:
                result = await session.execute(select(ExportEvent))
                return result.scalar_one()

        event = anyio.run(fetch_event)

        app.state.export_transport = MockTransport(
            lambda request: Response(200, request=Request("POST", request.url))
        )

        response = client.post(
            f"/v1/admin/export-dead-letter/{event.event_id}/replay",
            auth=("admin", "password"),
            headers={"Idempotency-Key": "export-replay-test"},
        )

        assert response.status_code == 202
        body = response.json()
        assert body["success"] is True
        assert body["event_id"] == event.event_id
        assert body["last_error_code"] is None

        async def fetch_updated():
            async with async_session_maker() as session:
                result = await session.execute(select(ExportEvent))
                return result.scalar_one()

        updated = anyio.run(fetch_updated)
        assert updated.replay_count == 1
        assert updated.last_replayed_by == "admin"
        assert updated.last_error_code is None
    finally:
        app.state.export_transport = original_transport
        settings.export_mode = original_mode
        settings.export_webhook_url = original_url
        settings.export_webhook_max_retries = original_retries
        settings.export_webhook_allow_http = original_allow_http
        settings.export_webhook_block_private_ips = original_block_private
        settings.admin_basic_username = original_admin_username
        settings.admin_basic_password = original_admin_password
        settings.outbox_max_attempts = original_outbox_attempts
