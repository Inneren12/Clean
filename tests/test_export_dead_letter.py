import anyio
from httpx import MockTransport, Request, Response
import anyio
from httpx import MockTransport, Request, Response
from sqlalchemy import select

from app.domain.export_events.db_models import ExportEvent
from app.infra.export import export_lead_async
from app.main import app
from app.settings import settings


def test_export_dead_letter_recorded_on_failure(async_session_maker):
    original_mode = settings.export_mode
    original_url = settings.export_webhook_url
    original_retries = settings.export_webhook_max_retries
    original_allow_http = settings.export_webhook_allow_http
    original_block_private = settings.export_webhook_block_private_ips
    original_env = settings.app_env

    settings.export_mode = "webhook"
    settings.export_webhook_url = "http://example.com/webhook"
    settings.export_webhook_max_retries = 2
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

        async def fetch_events():
            async with async_session_maker() as session:
                result = await session.execute(select(ExportEvent))
                return result.scalars().all()

        events = anyio.run(fetch_events)
        assert len(events) == 1
        event = events[0]
        assert event.lead_id == "lead-dead-letter"
        assert event.attempts == settings.export_webhook_max_retries
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


def test_export_dead_letter_endpoint_allows_dispatcher(client, async_session_maker):
    original_mode = settings.export_mode
    original_url = settings.export_webhook_url
    original_retries = settings.export_webhook_max_retries
    original_allow_http = settings.export_webhook_allow_http
    original_block_private = settings.export_webhook_block_private_ips
    original_allowed_hosts = settings.export_webhook_allowed_hosts
    original_dispatcher_username = settings.dispatcher_basic_username
    original_dispatcher_password = settings.dispatcher_basic_password

    settings.export_mode = "webhook"
    settings.export_webhook_url = "https://example.com/webhook"
    settings.export_webhook_max_retries = 2
    settings.export_webhook_allow_http = False
    settings.export_webhook_block_private_ips = False
    settings.export_webhook_allowed_hosts = ["example.com"]
    settings.dispatcher_basic_username = "dispatcher"
    settings.dispatcher_basic_password = "password"

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

        response = client.get(
            "/v1/admin/export-dead-letter",
            auth=("dispatcher", "password"),
        )
        assert response.status_code == 200
        events = response.json()
        event = next(
            (evt for evt in events if evt["lead_id"] == "lead-dead-letter-api"),
            None,
        )
        assert event is not None
        assert event["lead_id"] == "lead-dead-letter-api"
        assert event["mode"] == "webhook"
        assert event["target_url_host"] == "example.com"
        assert event["attempts"] == settings.export_webhook_max_retries
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


def test_export_dead_letter_replay(client, async_session_maker):
    original_mode = settings.export_mode
    original_url = settings.export_webhook_url
    original_retries = settings.export_webhook_max_retries
    original_allow_http = settings.export_webhook_allow_http
    original_block_private = settings.export_webhook_block_private_ips
    original_admin_username = settings.admin_basic_username
    original_admin_password = settings.admin_basic_password
    original_transport = getattr(app.state, "export_transport", None)

    settings.export_mode = "webhook"
    settings.export_webhook_url = "http://example.com/webhook"
    settings.export_webhook_max_retries = 2
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
