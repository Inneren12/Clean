from datetime import datetime, timedelta, timezone

from app.settings import settings


def test_booking_succeeds_when_email_fails(client, monkeypatch):
    future_start = (datetime.now(tz=timezone.utc) + timedelta(days=5)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )

    async def _boom(*_: object, **__: object):  # pragma: no cover - exercised via request
        raise RuntimeError("email_down")

    monkeypatch.setattr("app.domain.notifications.email_service.send_booking_pending_email", _boom)

    slots = client.get(
        "/v1/slots", params={"date": future_start.date().isoformat(), "time_on_site_hours": 1.5}
    )
    assert slots.status_code == 200
    chosen = slots.json()["slots"][0]

    response = client.post("/v1/bookings", json={"starts_at": chosen, "time_on_site_hours": 1.5})

    assert response.status_code == 201, response.text


def test_metrics_endpoint_returns_empty_when_disabled(client):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"
    settings.metrics_enabled = False

    auth = (settings.admin_basic_username, settings.admin_basic_password)
    response = client.get("/v1/admin/metrics", auth=auth)

    assert response.status_code == 200
    body = response.json()
    assert body["conversions"] == {
        "lead_created": 0,
        "booking_created": 0,
        "booking_confirmed": 0,
        "job_completed": 0,
    }
    assert body["accuracy"]["sample_size"] == 0


def test_auth_failure_returns_401_not_503(client):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"

    response = client.get("/v1/admin/leads")
    assert response.status_code == 401
