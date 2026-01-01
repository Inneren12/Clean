import base64
import datetime as dt
import uuid

import pytest
from app.domain.bookings.db_models import Team, TeamBlackout, TeamWorkingHours
from app.domain.saas.db_models import Organization
from app.domain.workers.db_models import Worker
from app.settings import settings


def _basic_auth(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture(autouse=True)
def _admin_credentials():
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"
    settings.dispatcher_basic_username = "admin"
    settings.dispatcher_basic_password = "secret"
    yield


async def _seed_org_workers(async_session_maker):
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()
    async with async_session_maker() as session:
        session.add_all(
            [
                Organization(org_id=org_a, name="Org A"),
                Organization(org_id=org_b, name="Org B"),
            ]
        )
        await session.flush()

        team_a = Team(name="Org A Team", org_id=org_a)
        team_b = Team(name="Org B Team", org_id=org_b)
        session.add_all([team_a, team_b])
        await session.flush()

        worker_a = Worker(
            name="Org A Worker",
            phone="+1 555-0001",
            email="a@example.com",
            team_id=team_a.team_id,
            org_id=org_a,
        )
        worker_b = Worker(
            name="Org B Worker",
            phone="+1 555-0002",
            email="b@example.com",
            team_id=team_b.team_id,
            org_id=org_b,
        )
        session.add_all([worker_a, worker_b])
        await session.flush()

        hours_a = TeamWorkingHours(
            team_id=team_a.team_id,
            day_of_week=1,
            start_time=dt.time(9, 0),
            end_time=dt.time(12, 0),
        )
        hours_b = TeamWorkingHours(
            team_id=team_b.team_id,
            day_of_week=2,
            start_time=dt.time(13, 0),
            end_time=dt.time(17, 0),
        )
        now = dt.datetime.now(tz=dt.timezone.utc)
        blackout_a = TeamBlackout(
            team_id=team_a.team_id,
            starts_at=now + dt.timedelta(days=1),
            ends_at=now + dt.timedelta(days=2),
            reason="Org A blackout",
        )
        blackout_b = TeamBlackout(
            team_id=team_b.team_id,
            starts_at=now + dt.timedelta(days=3),
            ends_at=now + dt.timedelta(days=4),
            reason="Org B blackout",
        )
        session.add_all([hours_a, hours_b, blackout_a, blackout_b])
        await session.commit()

    return {
        "org_a": org_a,
        "org_b": org_b,
        "team_a_id": team_a.team_id,
        "team_b_id": team_b.team_id,
        "worker_a_id": worker_a.worker_id,
        "worker_b_id": worker_b.worker_id,
        "blackout_a_id": blackout_a.id,
        "blackout_b_id": blackout_b.id,
    }


@pytest.mark.anyio
async def test_worker_list_and_detail_are_org_scoped(client, async_session_maker):
    seeded = await _seed_org_workers(async_session_maker)
    headers_a = {**_basic_auth("admin", "secret"), "X-Test-Org": str(seeded["org_a"])}
    headers_b = {**_basic_auth("admin", "secret"), "X-Test-Org": str(seeded["org_b"])}

    list_a = client.get("/v1/admin/ui/workers", headers=headers_a)
    assert list_a.status_code == 200
    assert "Org A Worker" in list_a.text
    assert "Org B Worker" not in list_a.text

    list_b = client.get("/v1/admin/ui/workers", headers=headers_b)
    assert list_b.status_code == 200
    assert "Org B Worker" in list_b.text
    assert "Org A Worker" not in list_b.text

    detail_other_org = client.get(
        f"/v1/admin/ui/workers/{seeded['worker_b_id']}", headers=headers_a
    )
    assert detail_other_org.status_code == 404

    update_other_org = client.post(
        f"/v1/admin/ui/workers/{seeded['worker_b_id']}",
        headers=headers_a,
        data={"name": "Wrong Org", "team_id": seeded["team_b_id"]},
        follow_redirects=False,
    )
    assert update_other_org.status_code == 404


@pytest.mark.anyio
async def test_worker_create_rejects_other_org_team(client, async_session_maker):
    seeded = await _seed_org_workers(async_session_maker)
    headers_a = {**_basic_auth("admin", "secret"), "X-Test-Org": str(seeded["org_a"])}

    create_resp = client.post(
        "/v1/admin/ui/workers/new",
        headers=headers_a,
        data={
            "name": "Cross Org Worker",
            "phone": "+1 555-9999",
            "team_id": seeded["team_b_id"],
        },
        follow_redirects=False,
    )
    assert create_resp.status_code == 404


@pytest.mark.anyio
async def test_team_schedule_endpoints_are_org_scoped(client, async_session_maker):
    seeded = await _seed_org_workers(async_session_maker)
    headers_a = {**_basic_auth("admin", "secret"), "X-Test-Org": str(seeded["org_a"])}
    headers_b = {**_basic_auth("admin", "secret"), "X-Test-Org": str(seeded["org_b"])}

    hours_a = client.get("/v1/admin/working-hours", headers=headers_a)
    assert hours_a.status_code == 200
    assert {row["team_id"] for row in hours_a.json()} == {seeded["team_a_id"]}

    hours_b = client.get("/v1/admin/working-hours", headers=headers_b)
    assert hours_b.status_code == 200
    assert {row["team_id"] for row in hours_b.json()} == {seeded["team_b_id"]}

    cross_update = client.post(
        "/v1/admin/working-hours",
        headers=headers_a,
        json={
            "team_id": seeded["team_b_id"],
            "day_of_week": 5,
            "start_time": "08:00:00",
            "end_time": "10:00:00",
        },
    )
    assert cross_update.status_code == 404

    blackouts_a = client.get("/v1/admin/blackouts", headers=headers_a)
    assert blackouts_a.status_code == 200
    assert {row["team_id"] for row in blackouts_a.json()} == {seeded["team_a_id"]}

    blackouts_b = client.get("/v1/admin/blackouts", headers=headers_b)
    assert blackouts_b.status_code == 200
    assert {row["team_id"] for row in blackouts_b.json()} == {seeded["team_b_id"]}

    cross_blackout = client.post(
        "/v1/admin/blackouts",
        headers=headers_a,
        json={
            "team_id": seeded["team_b_id"],
            "starts_at": (dt.datetime.now(tz=dt.timezone.utc) + dt.timedelta(days=5)).isoformat(),
            "ends_at": (dt.datetime.now(tz=dt.timezone.utc) + dt.timedelta(days=6)).isoformat(),
            "reason": "cross",
        },
    )
    assert cross_blackout.status_code == 404

    delete_other = client.delete(
        f"/v1/admin/blackouts/{seeded['blackout_b_id']}", headers=headers_a
    )
    assert delete_other.status_code == 404

    delete_own = client.delete(
        f"/v1/admin/blackouts/{seeded['blackout_a_id']}", headers=headers_a
    )
    assert delete_own.status_code == 204
