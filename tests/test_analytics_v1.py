import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

from app.settings import settings
from app.domain.saas.db_models import Organization
from app.domain.bookings.db_models import Booking, Team
from app.domain.leads.db_models import Lead
from app.domain.invoices.db_models import Payment
from app.domain.nps.db_models import NpsResponse
from app.domain.clients.db_models import ClientUser


def _auth() -> tuple[str, str]:
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"
    return (settings.admin_basic_username, settings.admin_basic_password)


def test_funnel_analytics_are_org_scoped_and_private(client, async_session_maker):
    auth = _auth()
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(tz=timezone.utc)
    start = now - timedelta(days=30)

    async def _seed() -> None:
        async with async_session_maker() as session:
            team_a = Team(org_id=org_a, name="Org A")
            team_b = Team(org_id=org_b, name="Org B")
            session.add_all([Organization(org_id=org_a, name="Org A"), Organization(org_id=org_b, name="Org B"), team_a, team_b])
            await session.flush()

            lead_a = Lead(
                org_id=org_a,
                name="Lead A",
                phone="780-000-0000",
                email="a@example.com",
                preferred_dates=["Mon"],
                structured_inputs={"beds": 1, "baths": 1, "cleaning_type": "standard"},
                estimate_snapshot={"total_before_tax": 120.0},
                pricing_config_version="v1",
                config_hash="hash",
                status="NEW",
                created_at=now - timedelta(days=5),
            )
            lead_b = Lead(
                org_id=org_b,
                name="Lead B",
                phone="780-000-0001",
                email="b@example.com",
                preferred_dates=["Tue"],
                structured_inputs={"beds": 1, "baths": 1, "cleaning_type": "standard"},
                estimate_snapshot={"total_before_tax": 140.0},
                pricing_config_version="v1",
                config_hash="hash",
                status="NEW",
                created_at=now - timedelta(days=4),
            )
            session.add_all([lead_a, lead_b])
            await session.flush()

            booking_a = Booking(
                org_id=org_a,
                team_id=team_a.team_id,
                lead_id=lead_a.lead_id,
                starts_at=now,
                duration_minutes=90,
                status="DONE",
                deposit_required=False,
                deposit_policy=[],
                created_at=now - timedelta(days=2),
                updated_at=now - timedelta(days=1),
            )
            booking_b = Booking(
                org_id=org_b,
                team_id=team_b.team_id,
                lead_id=lead_b.lead_id,
                starts_at=now,
                duration_minutes=90,
                status="CONFIRMED",
                deposit_required=False,
                deposit_policy=[],
                created_at=now - timedelta(days=3),
            )
            session.add_all([booking_a, booking_b])
            await session.flush()

            payment_a = Payment(
                booking_id=booking_a.booking_id,
                provider="test",
                method="card",
                amount_cents=1000,
                currency="USD",
                status="succeeded",
                received_at=now - timedelta(hours=1),
                org_id=org_a,
            )
            session.add(payment_a)
            await session.commit()

    asyncio.run(_seed())

    headers = {"X-Test-Org": str(org_a)}
    response = client.get(
        "/v1/admin/analytics/funnel",
        auth=auth,
        headers=headers,
        params={"from": start.isoformat()},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["counts"]["leads"] == 1
    assert body["counts"]["bookings"] == 1
    assert body["counts"]["completed"] == 1
    assert body["counts"]["paid"] == 1
    assert body["conversion_rates"]["lead_to_booking"] == 1.0
    serialized = json.dumps(body)
    assert "Lead A" not in serialized
    assert "a@example.com" not in serialized


def test_nps_analytics_distribution_and_trends(client, async_session_maker):
    auth = _auth()
    org_id = settings.default_org_id
    now = datetime.now(tz=timezone.utc)
    past_week = now - timedelta(days=7)
    past_month = now - timedelta(days=30)

    async def _seed() -> None:
        async with async_session_maker() as session:
            booking_recent = Booking(
                org_id=org_id,
                team_id=1,
                starts_at=now,
                duration_minutes=60,
                status="DONE",
                deposit_required=False,
                deposit_policy=[],
                created_at=now - timedelta(days=1),
            )
            booking_previous = Booking(
                org_id=org_id,
                team_id=1,
                starts_at=past_month,
                duration_minutes=60,
                status="DONE",
                deposit_required=False,
                deposit_policy=[],
                created_at=past_month,
            )
            other_org = uuid.uuid4()
            other_team = Team(org_id=other_org, name="Other")
            session.add_all([other_team, booking_recent, booking_previous])
            await session.flush()
            other_booking = Booking(
                org_id=other_org,
                team_id=other_team.team_id,
                starts_at=now,
                duration_minutes=60,
                status="DONE",
                deposit_required=False,
                deposit_policy=[],
            )
            session.add(other_booking)
            await session.flush()

            session.add_all(
                [
                    NpsResponse(order_id=booking_recent.booking_id, score=10, created_at=now),
                    NpsResponse(order_id=booking_previous.booking_id, score=5, created_at=past_month),
                    NpsResponse(order_id=other_booking.booking_id, score=1, created_at=now),
                ]
            )
            await session.commit()

    asyncio.run(_seed())

    response = client.get(
        "/v1/admin/analytics/nps",
        auth=auth,
        params={"from": past_month.isoformat()},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["distribution"]["total_responses"] == 2
    assert body["distribution"]["promoters"] == 1
    assert body["distribution"]["detractors"] == 1
    assert body["distribution"]["promoter_rate"] == 0.5
    assert len(body["trends"]["weekly"]) >= 1
    assert len(body["trends"]["monthly"]) >= 1
    serialized = json.dumps(body)
    assert "comment" not in serialized


def test_cohort_repeat_rates_respect_org_scope(client, async_session_maker):
    auth = _auth()
    org_c, org_d = uuid.uuid4(), uuid.uuid4()
    jan_start = datetime(2024, 1, 15, tzinfo=timezone.utc)
    feb_start = datetime(2024, 2, 10, tzinfo=timezone.utc)
    end = datetime(2024, 3, 1, tzinfo=timezone.utc)

    async def _seed() -> None:
        async with async_session_maker() as session:
            team_c = Team(org_id=org_c, name="C")
            team_d = Team(org_id=org_d, name="D")
            client_one = ClientUser(email="client1@example.com", name="Client One")
            client_two = ClientUser(email="client2@example.com", name="Client Two")
            session.add_all(
                [
                    Organization(org_id=org_c, name="Org C"),
                    Organization(org_id=org_d, name="Org D"),
                    team_c,
                    team_d,
                    client_one,
                    client_two,
                ]
            )
            await session.flush()

            lead_one = Lead(
                org_id=org_c,
                name="Lead Cohort 1",
                phone="780-222-0000",
                email="cohort1@example.com",
                preferred_dates=["Wed"],
                structured_inputs={"beds": 2, "baths": 1, "cleaning_type": "standard"},
                estimate_snapshot={"total_before_tax": 160.0},
                pricing_config_version="v1",
                config_hash="hash",
                status="NEW",
                created_at=jan_start,
            )
            lead_two = Lead(
                org_id=org_c,
                name="Lead Cohort 2",
                phone="780-333-0000",
                email="cohort2@example.com",
                preferred_dates=["Thu"],
                structured_inputs={"beds": 1, "baths": 1, "cleaning_type": "standard"},
                estimate_snapshot={"total_before_tax": 100.0},
                pricing_config_version="v1",
                config_hash="hash",
                status="NEW",
                created_at=feb_start,
            )
            session.add_all([lead_one, lead_two])
            await session.flush()

            session.add_all(
                [
                    Booking(
                        org_id=org_c,
                        team_id=team_c.team_id,
                        lead_id=lead_one.lead_id,
                        client_id=client_one.client_id,
                        starts_at=jan_start,
                        duration_minutes=60,
                        status="DONE",
                        deposit_required=False,
                        deposit_policy=[],
                        created_at=jan_start,
                    ),
                    Booking(
                        org_id=org_c,
                        team_id=team_c.team_id,
                        lead_id=lead_one.lead_id,
                        client_id=client_one.client_id,
                        starts_at=jan_start + timedelta(days=10),
                        duration_minutes=60,
                        status="DONE",
                        deposit_required=False,
                        deposit_policy=[],
                        created_at=jan_start + timedelta(days=10),
                    ),
                    Booking(
                        org_id=org_c,
                        team_id=team_c.team_id,
                        lead_id=lead_two.lead_id,
                        client_id=client_two.client_id,
                        starts_at=feb_start,
                        duration_minutes=60,
                        status="CONFIRMED",
                        deposit_required=False,
                        deposit_policy=[],
                        created_at=feb_start,
                    ),
                    Booking(
                        org_id=org_d,
                        team_id=team_d.team_id,
                        starts_at=jan_start,
                        duration_minutes=60,
                        status="DONE",
                        deposit_required=False,
                        deposit_policy=[],
                    ),
                ]
            )
            await session.commit()

    asyncio.run(_seed())

    response = client.get(
        "/v1/admin/analytics/cohorts",
        auth=auth,
        headers={"X-Test-Org": str(org_c)},
        params={"from": jan_start.isoformat(), "to": end.isoformat()},
    )
    assert response.status_code == 200
    body = response.json()
    jan_cohort = next(item for item in body["cohorts"] if item["cohort_month"].startswith("2024-01"))
    feb_cohort = next(item for item in body["cohorts"] if item["cohort_month"].startswith("2024-02"))
    assert jan_cohort["customers"] == 1
    assert jan_cohort["repeat_customers"] == 1
    assert jan_cohort["repeat_rate"] == 1.0
    assert feb_cohort["customers"] == 1
    assert feb_cohort["repeat_customers"] == 0
    assert feb_cohort["repeat_rate"] == 0.0
    serialized = json.dumps(body)
    assert "Client One" not in serialized
    assert "client1@example.com" not in serialized
