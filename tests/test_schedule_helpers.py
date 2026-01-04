import base64
import datetime as dt
import uuid

import pytest

from app.domain.bookings.db_models import Booking, TeamBlackout, Team
from app.domain.bookings.service import ensure_default_team
from app.domain.saas.db_models import Organization
from app.domain.workers.db_models import Worker
from app.settings import settings


def _basic_auth(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture(autouse=True)
def _dispatch_creds():
    original_username = settings.dispatcher_basic_username
    original_password = settings.dispatcher_basic_password
    settings.dispatcher_basic_username = "dispatch"
    settings.dispatcher_basic_password = "secret"
    yield
    settings.dispatcher_basic_username = original_username
    settings.dispatcher_basic_password = original_password


@pytest.mark.anyio
async def test_conflict_check_catches_overlap(client, async_session_maker):
    async with async_session_maker() as session:
        team = await ensure_default_team(session)
        start = dt.datetime.now(tz=dt.timezone.utc) + dt.timedelta(days=1)
        booking = Booking(
            team_id=team.team_id,
            starts_at=start,
            duration_minutes=90,
            status="PENDING",
        )
        session.add(booking)
        session.add(
            TeamBlackout(
                team_id=team.team_id,
                starts_at=start - dt.timedelta(minutes=30),
                ends_at=start + dt.timedelta(minutes=30),
                reason="maintenance",
            )
        )
        await session.commit()

    headers = _basic_auth("dispatch", "secret")
    params = {
        "starts_at": start.isoformat(),
        "ends_at": (start + dt.timedelta(minutes=60)).isoformat(),
        "team_id": team.team_id,
    }
    resp = client.get("/v1/admin/schedule/conflicts", headers=headers, params=params)
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_conflict"] is True
    kinds = {item["kind"] for item in body["conflicts"]}
    assert {"booking", "blackout"}.issubset(kinds)


@pytest.mark.anyio
async def test_suggestions_filter_by_conflict_and_skill(client, async_session_maker):
    async with async_session_maker() as session:
        team = await ensure_default_team(session)
        secondary_team = Team(name="Secondary", org_id=team.org_id)
        session.add(secondary_team)
        await session.flush()
        available_worker = Worker(
            name="Available", phone="+1 555", team_id=team.team_id, email="a@example.com", role="deep clean", is_active=True
        )
        busy_worker = Worker(
            name="Busy", phone="+1 666", team_id=secondary_team.team_id, email="b@example.com", role="deep clean", is_active=True
        )
        other_skill = Worker(
            name="Other Skill", phone="+1 777", team_id=team.team_id, email="c@example.com", role="standard", is_active=True
        )
        session.add_all([available_worker, busy_worker, other_skill])
        await session.flush()

        conflict_start = dt.datetime.now(tz=dt.timezone.utc) + dt.timedelta(days=2)
        session.add(
            Booking(
                team_id=secondary_team.team_id,
                assigned_worker_id=busy_worker.worker_id,
                starts_at=conflict_start,
                duration_minutes=120,
                status="PENDING",
            )
        )
        await session.commit()

    headers = _basic_auth("dispatch", "secret")
    params = {
        "starts_at": conflict_start.isoformat(),
        "ends_at": (conflict_start + dt.timedelta(minutes=90)).isoformat(),
        "skill_tags": ["deep"],
    }
    resp = client.get("/v1/admin/schedule/suggestions", headers=headers, params=params)
    assert resp.status_code == 200
    body = resp.json()
    worker_names = {worker["name"] for worker in body["workers"]}
    assert "Available" in worker_names
    assert "Busy" not in worker_names
    assert "Other Skill" not in worker_names


@pytest.mark.anyio
async def test_conflicts_and_suggestions_are_org_scoped(client, async_session_maker):
    other_org_id = uuid.uuid4()
    async with async_session_maker() as session:
        default_team = await ensure_default_team(session)
        other_org = Organization(org_id=other_org_id, name="Other Org")
        session.add(other_org)
        await session.flush()

        other_team = Team(name="Other Org Team", org_id=other_org.org_id)
        session.add(other_team)
        await session.flush()
        start = dt.datetime.now(tz=dt.timezone.utc) + dt.timedelta(days=3)
        session.add(
            Booking(
                team_id=other_team.team_id,
                org_id=other_org_id,
                starts_at=start,
                duration_minutes=60,
                status="PENDING",
            )
        )
        await session.commit()

    headers = _basic_auth("dispatch", "secret")
    window = {
        "starts_at": start.isoformat(),
        "ends_at": (start + dt.timedelta(minutes=60)).isoformat(),
    }
    conflict_resp = client.get(
        "/v1/admin/schedule/conflicts",
        headers=headers,
        params={"team_id": default_team.team_id, **window},
    )
    assert conflict_resp.status_code == 200
    conflict_body = conflict_resp.json()
    assert conflict_body["has_conflict"] is False

    suggestion_resp = client.get("/v1/admin/schedule/suggestions", headers=headers, params=window)
    assert suggestion_resp.status_code == 200
    suggestion_body = suggestion_resp.json()
    team_ids = {team["team_id"] for team in suggestion_body["teams"]}
    assert default_team.team_id in team_ids
    assert all(team_id != other_team.team_id for team_id in team_ids)
