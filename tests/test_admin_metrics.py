import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.settings import settings
from app.domain.bookings.db_models import Booking
from app.domain.analytics.db_models import EventLog
from app.domain.analytics.service import EventType
from app.domain.leads.db_models import Lead
from app.domain.saas.db_models import Organization
from app.domain.bookings.db_models import Team


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


def test_admin_metrics_are_org_scoped(client, async_session_maker):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"
    org_a, org_b = uuid.uuid4(), uuid.uuid4()

    async def _seed_events() -> None:
        async with async_session_maker() as session:
            session.add_all([Organization(org_id=org_a, name="Org A"), Organization(org_id=org_b, name="Org B")])
            team_a = Team(org_id=org_a, name="Team A")
            team_b = Team(org_id=org_b, name="Team B")
            session.add_all([team_a, team_b])
            await session.flush()

            lead_a = Lead(
                org_id=org_a,
                name="Lead A",
                phone="780-555-0001",
                email="lead-a@example.com",
                postal_code="T5A",
                preferred_dates=["Mon"],
                structured_inputs={"beds": 1, "baths": 1, "cleaning_type": "standard"},
                estimate_snapshot={"total_before_tax": 100.0},
                pricing_config_version="v1",
                config_hash="hash",
                status="NEW",
            )
            lead_b = Lead(
                org_id=org_b,
                name="Lead B",
                phone="780-555-0002",
                email="lead-b@example.com",
                postal_code="T5B",
                preferred_dates=["Tue"],
                structured_inputs={"beds": 2, "baths": 1, "cleaning_type": "standard"},
                estimate_snapshot={"total_before_tax": 200.0},
                pricing_config_version="v1",
                config_hash="hash",
                status="NEW",
            )
            session.add_all([lead_a, lead_b])
            await session.flush()

            booking_a = Booking(
                org_id=org_a,
                team_id=team_a.team_id,
                lead_id=lead_a.lead_id,
                starts_at=datetime.now(tz=timezone.utc),
                duration_minutes=90,
                actual_duration_minutes=80,
                status="DONE",
                deposit_required=False,
                deposit_policy=[],
            )
            booking_b = Booking(
                org_id=org_b,
                team_id=team_b.team_id,
                lead_id=lead_b.lead_id,
                starts_at=datetime.now(tz=timezone.utc),
                duration_minutes=45,
                actual_duration_minutes=40,
                status="DONE",
                deposit_required=False,
                deposit_policy=[],
            )
            session.add_all([booking_a, booking_b])
            await session.flush()

            now = datetime.now(tz=timezone.utc)
            session.add_all(
                [
                    EventLog(event_type=EventType.lead_created.value, lead_id=lead_a.lead_id, occurred_at=now),
                    EventLog(event_type=EventType.booking_created.value, booking_id=booking_a.booking_id, occurred_at=now),
                    EventLog(event_type=EventType.lead_created.value, lead_id=lead_b.lead_id, occurred_at=now),
                ]
            )

            await session.commit()

    asyncio.run(_seed_events())
    headers = {"X-Test-Org": str(org_a)}

    metrics_response = client.get("/v1/admin/metrics", auth=("admin", "secret"), headers=headers)
    assert metrics_response.status_code == 200
    metrics = metrics_response.json()

    assert metrics["conversions"]["lead_created"] == 1
    assert metrics["conversions"]["booking_created"] == 1

    csv_response = client.get("/v1/admin/metrics?format=csv", auth=("admin", "secret"), headers=headers)
    assert csv_response.status_code == 200
    assert "text/csv" in csv_response.headers.get("content-type", "")
    csv_body = csv_response.text
    assert "lead_created,1" in csv_body
    assert "booking_created,1" in csv_body


def test_admin_metrics_reports_kpi_aggregates(client, async_session_maker):
    auth = _auth()
    base = datetime(2024, 2, 1, tzinfo=timezone.utc)
    range_start = base - timedelta(days=60)
    range_end = base

    estimate_one = _create_estimate(client)
    estimate_one["labor_cost"] = 100.0
    lead_one_response = client.post(
        "/v1/leads",
        json={
            "name": "Retention One",
            "phone": "111-555-1212",
            "email": "retention1@example.com",
            "preferred_dates": ["Mon"],
            "structured_inputs": {"beds": 2, "baths": 1, "cleaning_type": "standard"},
            "estimate_snapshot": estimate_one,
            "utm_source": "adwords",
        },
    )
    assert lead_one_response.status_code == 201
    lead_one = lead_one_response.json()["lead_id"]

    estimate_two = _create_estimate(client)
    estimate_two["labor_cost"] = 80.0
    lead_two_response = client.post(
        "/v1/leads",
        json={
            "name": "Retention Two",
            "phone": "222-555-1212",
            "email": "retention2@example.com",
            "preferred_dates": ["Tue"],
            "structured_inputs": {"beds": 3, "baths": 2, "cleaning_type": "deep"},
            "estimate_snapshot": estimate_two,
            "utm_source": "search",
        },
    )
    assert lead_two_response.status_code == 201
    lead_two = lead_two_response.json()["lead_id"]

    async def seed_history() -> None:
        async with async_session_maker() as session:
            bookings: list[Booking] = [
                Booking(
                    team_id=1,
                    lead_id=lead_one,
                    starts_at=base - timedelta(days=50),
                    duration_minutes=120,
                    planned_minutes=120,
                    actual_duration_minutes=150,
                    status="DONE",
                    base_charge_cents=40000,
                    refund_total_cents=5000,
                    deposit_required=False,
                    deposit_policy=[],
                ),
                Booking(
                    team_id=1,
                    lead_id=lead_one,
                    starts_at=base - timedelta(days=10),
                    duration_minutes=90,
                    planned_minutes=90,
                    actual_duration_minutes=80,
                    status="DONE",
                    base_charge_cents=20000,
                    deposit_required=False,
                    deposit_policy=[],
                ),
                Booking(
                    team_id=1,
                    lead_id=lead_two,
                    starts_at=base - timedelta(days=25),
                    duration_minutes=100,
                    planned_minutes=100,
                    actual_duration_minutes=100,
                    status="DONE",
                    base_charge_cents=30000,
                    deposit_required=False,
                    deposit_policy=[],
                ),
                Booking(
                    team_id=1,
                    lead_id=lead_two,
                    starts_at=base - timedelta(days=5),
                    duration_minutes=60,
                    planned_minutes=60,
                    actual_duration_minutes=70,
                    status="DONE",
                    base_charge_cents=25000,
                    deposit_required=False,
                    deposit_policy=[],
                ),
                Booking(
                    team_id=1,
                    lead_id=lead_one,
                    starts_at=base - timedelta(days=3),
                    duration_minutes=45,
                    planned_minutes=45,
                    status="CANCELLED",
                    deposit_required=False,
                    deposit_policy=[],
                ),
            ]
            session.add_all(bookings)
            await session.commit()

    asyncio.run(seed_history())

    metrics_response = client.get(
        "/v1/admin/metrics",
        auth=auth,
        params={"from": range_start.isoformat(), "to": range_end.isoformat()},
    )
    assert metrics_response.status_code == 200
    metrics = metrics_response.json()

    assert metrics["financial"]["total_revenue_cents"] == 110000
    assert metrics["financial"]["revenue_per_day_cents"] == 1833.33
    assert metrics["financial"]["margin_cents"] == 74000
    assert metrics["financial"]["average_order_value_cents"] == 27500.0

    assert metrics["operational"]["crew_utilization"] == 1.0811
    assert metrics["operational"]["cancellation_rate"] == 0.2
    assert metrics["operational"]["retention_30_day"] == 0.5
    assert metrics["operational"]["retention_60_day"] == 1.0
    assert metrics["operational"]["retention_90_day"] == 1.0
