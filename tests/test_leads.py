import asyncio

from sqlalchemy import select

from app.settings import settings

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

    settings.export_mode = "webhook"
    settings.export_webhook_url = "http://127.0.0.1:1/"
    settings.export_webhook_timeout_seconds = 1
    settings.export_webhook_max_retries = 1
    settings.export_webhook_backoff_seconds = 0.1

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
