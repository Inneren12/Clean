from httpx import MockTransport, Response

from app.main import app
from app.settings import settings


def _lead_payload(client):
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

    return {
        "name": "Captcha Test",
        "phone": "780-555-0000",
        "email": "captcha@example.com",
        "preferred_dates": ["Sat morning"],
        "structured_inputs": {"beds": 1, "baths": 1, "cleaning_type": "standard"},
        "estimate_snapshot": estimate,
    }


def test_leads_captcha_off_allows(client):
    original_mode = settings.captcha_mode
    original_secret = settings.turnstile_secret_key
    original_transport = getattr(app.state, "turnstile_transport", None)

    settings.captcha_mode = "off"
    settings.turnstile_secret_key = None
    app.state.turnstile_transport = None

    try:
        response = client.post("/v1/leads", json=_lead_payload(client))
        assert response.status_code == 201
    finally:
        settings.captcha_mode = original_mode
        settings.turnstile_secret_key = original_secret
        app.state.turnstile_transport = original_transport


def test_leads_captcha_turnstile_requires_token(client):
    original_mode = settings.captcha_mode
    original_secret = settings.turnstile_secret_key
    original_transport = getattr(app.state, "turnstile_transport", None)

    settings.captcha_mode = "turnstile"
    settings.turnstile_secret_key = "secret-key"
    app.state.turnstile_transport = None

    try:
        response = client.post("/v1/leads", json=_lead_payload(client))
        assert response.status_code == 400
    finally:
        settings.captcha_mode = original_mode
        settings.turnstile_secret_key = original_secret
        app.state.turnstile_transport = original_transport


def test_leads_captcha_turnstile_accepts_valid_token(client):
    original_mode = settings.captcha_mode
    original_secret = settings.turnstile_secret_key
    original_transport = getattr(app.state, "turnstile_transport", None)

    settings.captcha_mode = "turnstile"
    settings.turnstile_secret_key = "secret-key"
    app.state.turnstile_transport = MockTransport(
        lambda request: Response(200, request=request, json={"success": True})
    )

    lead_payload = _lead_payload(client)
    lead_payload["captcha_token"] = "ok"

    try:
        response = client.post("/v1/leads", json=lead_payload)
        assert response.status_code == 201
    finally:
        settings.captcha_mode = original_mode
        settings.turnstile_secret_key = original_secret
        app.state.turnstile_transport = original_transport
