import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.settings import settings
from app.domain.bookings.db_models import Booking


def _auth() -> tuple[str, str]:
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"
    return (settings.admin_basic_username, settings.admin_basic_password)


def _create_estimate(client):
    response = client.post(
        "/v1/estimate",
        json={
            "beds": 2,
            "baths": 1.5,
            "cleaning_type": "standard",
            "heavy_grease": False,
            "multi_floor": False,
            "frequency": "one_time",
            "add_ons": {"oven": True},
        },
    )
    assert response.status_code == 200
    return response.json()


def test_admin_metrics_reports_conversions_and_accuracy(client, async_session_maker):
    auth = _auth()
    estimate = _create_estimate(client)
    total_cents = int(round(float(estimate["total_before_tax"]) * 100))

    lead_response = client.post(
        "/v1/leads",
        json={
            "name": "Metrics User",
            "phone": "780-555-1212",
            "email": "metrics@example.com",
            "preferred_dates": ["Fri"],
            "structured_inputs": {"beds": 2, "baths": 1, "cleaning_type": "standard"},
            "estimate_snapshot": estimate,
            "utm_source": "adwords",
        },
    )
    assert lead_response.status_code == 201
    lead_id = lead_response.json()["lead_id"]

    async def _seed_history() -> None:
        async with async_session_maker() as session:
            booking = Booking(
                team_id=1,
                lead_id=lead_id,
                starts_at=datetime.now(tz=timezone.utc) - timedelta(days=10),
                duration_minutes=60,
                status="DONE",
                deposit_required=False,
                deposit_policy=[],
            )
            session.add(booking)
            await session.commit()

    asyncio.run(_seed_history())

    local_tz = ZoneInfo("America/Edmonton")
    start_time_local = datetime.now(tz=local_tz).replace(
        hour=10, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)
    while start_time_local.weekday() >= 5:
        start_time_local += timedelta(days=1)
    start_time = start_time_local.astimezone(timezone.utc)
    booking_response = client.post(
        "/v1/bookings",
        json={
            "starts_at": start_time.isoformat(),
            "time_on_site_hours": 2.0,
            "lead_id": lead_id,
        },
    )
    assert booking_response.status_code == 201
    booking = booking_response.json()

    confirm_response = client.post(
        f"/v1/admin/bookings/{booking['booking_id']}/confirm",
        auth=auth,
    )
    assert confirm_response.status_code == 200

    confirm_response_repeat = client.post(
        f"/v1/admin/bookings/{booking['booking_id']}/confirm",
        auth=auth,
    )
    assert confirm_response_repeat.status_code == 200

    complete_response = client.post(
        f"/v1/admin/bookings/{booking['booking_id']}/complete",
        json={"actual_duration_minutes": 150},
        auth=auth,
    )
    assert complete_response.status_code == 200
    completed = complete_response.json()
    assert completed["actual_duration_minutes"] == 150
    assert completed["status"] == "DONE"

    metrics_response = client.get("/v1/admin/metrics", auth=auth)
    assert metrics_response.status_code == 200
    metrics = metrics_response.json()

    assert metrics["conversions"]["lead_created"] == 1
    assert metrics["conversions"]["booking_created"] == 1
    assert metrics["conversions"]["booking_confirmed"] == 1
    assert metrics["conversions"]["job_completed"] == 1
    assert metrics["revenue"]["average_estimated_revenue_cents"] == total_cents
    assert metrics["accuracy"]["sample_size"] == 1
    assert metrics["accuracy"]["average_actual_duration_minutes"] == 150.0
    assert metrics["accuracy"]["average_estimated_duration_minutes"] == 120.0

    csv_response = client.get("/v1/admin/metrics?format=csv", auth=auth)
    assert csv_response.status_code == 200
    assert "text/csv" in csv_response.headers.get("content-type", "")
    csv_body = csv_response.text
    assert "lead_created,1" in csv_body
    assert "booking_confirmed,1" in csv_body
