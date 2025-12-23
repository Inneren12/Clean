import asyncio

import httpx
from sqlalchemy import select

from app.settings import settings
from app.main import app

from app.domain.leads.db_models import Lead


def test_create_lead_persists_snapshot(client, async_session_maker):
    estimate_response = client.post(
        "/v1/estimate",
        json={
            "beds": 2,
            "baths": 2,
            "cleaning_type": "deep",
            "heavy_grease": False,
            "multi_floor": False,
            "frequency": "one_time",
            "add_ons": {"oven": True, "fridge": True},
        },
    )
    assert estimate_response.status_code == 200
    estimate = estimate_response.json()

    lead_payload = {
        "name": "Jamie Customer",
        "phone": "780-555-2222",
        "email": "jamie@example.com",
        "preferred_dates": ["Sat morning", "Sun afternoon"],
        "structured_inputs": {"beds": 2, "baths": 2, "cleaning_type": "deep"},
        "estimate_snapshot": estimate,
    }

    response = client.post("/v1/leads", json=lead_payload)
    assert response.status_code == 201
    lead_id = response.json()["lead_id"]

    async def fetch_lead():
        async with async_session_maker() as session:
            result = await session.execute(select(Lead).where(Lead.lead_id == lead_id))
            return result.scalar_one()

    lead = asyncio.run(fetch_lead())
    assert lead.pricing_config_version == estimate["pricing_config_version"]
    assert lead.config_hash == estimate["config_hash"]


def test_create_lead_succeeds_when_webhook_export_fails(client):
    original_mode = settings.export_mode
    original_url = settings.export_webhook_url
    original_timeout = settings.export_webhook_timeout_seconds
    original_retries = settings.export_webhook_max_retries
    original_backoff = settings.export_webhook_backoff_seconds
    original_allowed_hosts = settings.export_webhook_allowed_hosts
    original_block_private_ips = settings.export_webhook_block_private_ips
    original_allow_http = settings.export_webhook_allow_http

    settings.export_mode = "webhook"
    settings.export_webhook_url = "https://hook.test/lead"
    settings.export_webhook_timeout_seconds = 1
    settings.export_webhook_max_retries = 1
    settings.export_webhook_backoff_seconds = 0.1
    settings.export_webhook_allowed_hosts = ["hook.test"]
    settings.export_webhook_allow_http = False
    settings.export_webhook_block_private_ips = True
    app.state.export_resolver = lambda host: ["203.0.113.10"]
    def _fail_transport(request):
        raise httpx.ConnectError("boom", request=request)

    app.state.export_transport = httpx.MockTransport(_fail_transport)

    try:
        estimate_response = client.post(
            "/v1/estimate",
            json={
                "beds": 1,
                "baths": 1,
                "cleaning_type": "standard",
                "heavy_grease": False,
                "multi_floor": False,
                "frequency": "one_time",
                "add_ons": {},
            },
        )
        assert estimate_response.status_code == 200
        estimate = estimate_response.json()

        lead_payload = {
            "name": "Webhook Failure",
            "phone": "780-555-3333",
            "email": "webhook@example.com",
            "preferred_dates": ["Mon afternoon"],
            "structured_inputs": {"beds": 1, "baths": 1, "cleaning_type": "standard"},
            "estimate_snapshot": estimate,
        }

        response = client.post("/v1/leads", json=lead_payload)
        assert response.status_code == 201
        assert "lead_id" in response.json()
    finally:
        settings.export_mode = original_mode
        settings.export_webhook_url = original_url
        settings.export_webhook_timeout_seconds = original_timeout
        settings.export_webhook_max_retries = original_retries
        settings.export_webhook_backoff_seconds = original_backoff
        settings.export_webhook_allowed_hosts = original_allowed_hosts
        settings.export_webhook_block_private_ips = original_block_private_ips
        settings.export_webhook_allow_http = original_allow_http
        app.state.export_resolver = None
        app.state.export_transport = None


def test_create_lead_succeeds_when_allowlist_required_skips_export(client):
    original_app_env = settings.app_env
    original_mode = settings.export_mode
    original_url = settings.export_webhook_url
    original_allowed_hosts = settings.export_webhook_allowed_hosts
    original_block_private_ips = settings.export_webhook_block_private_ips
    original_allow_http = settings.export_webhook_allow_http

    settings.app_env = "prod"
    settings.export_mode = "webhook"
    settings.export_webhook_url = "https://hook.test/lead"
    settings.export_webhook_allowed_hosts = []
    settings.export_webhook_allow_http = False
    settings.export_webhook_block_private_ips = True
    app.state.export_resolver = lambda host: ["198.51.100.10"]
    app.state.export_transport = httpx.MockTransport(lambda request: httpx.Response(204))

    try:
        estimate_response = client.post(
            "/v1/estimate",
            json={
                "beds": 1,
                "baths": 1,
                "cleaning_type": "standard",
                "heavy_grease": False,
                "multi_floor": False,
                "frequency": "one_time",
                "add_ons": {},
            },
        )
        assert estimate_response.status_code == 200
        estimate = estimate_response.json()

        lead_payload = {
            "name": "Allowlist Required",
            "phone": "780-555-6666",
            "email": "allowlist@example.com",
            "preferred_dates": ["Thu afternoon"],
            "structured_inputs": {"beds": 1, "baths": 1, "cleaning_type": "standard"},
            "estimate_snapshot": estimate,
        }

        response = client.post("/v1/leads", json=lead_payload)
        assert response.status_code == 201
        assert "lead_id" in response.json()
    finally:
        settings.app_env = original_app_env
        settings.export_mode = original_mode
        settings.export_webhook_url = original_url
        settings.export_webhook_allowed_hosts = original_allowed_hosts
        settings.export_webhook_block_private_ips = original_block_private_ips
        settings.export_webhook_allow_http = original_allow_http
        app.state.export_resolver = None
        app.state.export_transport = None


def test_create_lead_rejects_invalid_structured_inputs_key(client):
    estimate_response = client.post(
        "/v1/estimate",
        json={
            "beds": 2,
            "baths": 1,
            "cleaning_type": "standard",
            "heavy_grease": False,
            "multi_floor": False,
            "frequency": "one_time",
            "add_ons": {},
        },
    )
    assert estimate_response.status_code == 200
    estimate = estimate_response.json()

    lead_payload = {
        "name": "Bad Structured Input",
        "phone": "780-555-1111",
        "email": "bad@example.com",
        "preferred_dates": ["Tue morning"],
        "structured_inputs": {
            "beds": 2,
            "baths": 1,
            "cleaning_type": "standard",
            "unexpected": "nope",
        },
        "estimate_snapshot": estimate,
    }

    response = client.post("/v1/leads", json=lead_payload)
    assert response.status_code == 422
    body = response.json()
    assert body["title"] == "Validation Error"
    assert any(error["field"].startswith("structured_inputs") for error in body["errors"])


def test_create_lead_accepts_minimal_structured_inputs(client):
    estimate_response = client.post(
        "/v1/estimate",
        json={
            "beds": 1,
            "baths": 1,
            "cleaning_type": "standard",
            "heavy_grease": False,
            "multi_floor": False,
            "frequency": "one_time",
            "add_ons": {},
        },
    )
    assert estimate_response.status_code == 200
    estimate = estimate_response.json()

    lead_payload = {
        "name": "Minimal Structured Input",
        "phone": "780-555-4444",
        "email": "minimal@example.com",
        "preferred_dates": ["Wed evening"],
        "structured_inputs": {"beds": 1, "baths": 1},
        "estimate_snapshot": estimate,
    }

    response = client.post("/v1/leads", json=lead_payload)
    assert response.status_code == 201
