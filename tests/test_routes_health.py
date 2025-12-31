import asyncio

import pytest
from sqlalchemy import text

from datetime import datetime, timedelta, timezone

from app.api import routes_health
from app.domain.ops.db_models import JobHeartbeat
from app.settings import settings


async def _set_alembic_version(async_session_maker, version: str | None) -> None:
    async with async_session_maker() as session:
        await session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)"
            )
        )
        await session.execute(text("DELETE FROM alembic_version"))
        if version is not None:
            await session.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:version)"),
                {"version": version},
            )
        await session.commit()


async def _set_job_heartbeat(async_session_maker, age_seconds: int = 0) -> None:
    async with async_session_maker() as session:
        heartbeat = JobHeartbeat(
            name="jobs-runner",
            last_heartbeat=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
        )
        await session.merge(heartbeat)
        await session.commit()


@pytest.fixture(autouse=True)
def reset_head_cache():
    routes_health._HEAD_CACHE.update(
        {"timestamp": 0.0, "heads": None, "skip_reason": None, "warning_logged": False}
    )
    yield
    routes_health._HEAD_CACHE.update(
        {"timestamp": 0.0, "heads": None, "skip_reason": None, "warning_logged": False}
    )


def test_readyz_single_head_current(monkeypatch, client, async_session_maker):
    asyncio.run(_set_alembic_version(async_session_maker, "head1"))
    monkeypatch.setattr(routes_health, "_load_expected_heads", lambda: (["head1"], None))

    response = client.get("/readyz")

    assert response.status_code == 200
    payload = response.json()["database"]
    assert payload["migrations_current"] is True
    assert payload["expected_head"] == "head1"
    assert payload["expected_heads"] == ["head1"]
    assert payload["migrations_check"] == "ok"


def test_readyz_single_head_behind(monkeypatch, client, async_session_maker):
    asyncio.run(_set_alembic_version(async_session_maker, "base"))
    monkeypatch.setattr(routes_health, "_load_expected_heads", lambda: (["head2"], None))

    response = client.get("/readyz")

    assert response.status_code == 503
    payload = response.json()["database"]
    assert payload["migrations_current"] is False
    assert payload["expected_head"] == "head2"
    assert payload["expected_heads"] == ["head2"]


def test_readyz_multi_head_current(monkeypatch, client, async_session_maker):
    asyncio.run(_set_alembic_version(async_session_maker, "h2"))
    monkeypatch.setattr(routes_health, "_load_expected_heads", lambda: (["h1", "h2"], None))

    response = client.get("/readyz")

    assert response.status_code == 200
    payload = response.json()["database"]
    assert payload["migrations_current"] is True
    assert payload["expected_head"] is None
    assert set(payload["expected_heads"]) == {"h1", "h2"}


def test_readyz_alembic_unavailable(monkeypatch, client, async_session_maker):
    asyncio.run(_set_alembic_version(async_session_maker, None))
    monkeypatch.setattr(
        routes_health, "_load_expected_heads", lambda: ([], "skipped_no_alembic_files")
    )

    response = client.get("/readyz")

    assert response.status_code == 200
    payload = response.json()["database"]
    assert payload["ok"] is True
    assert payload["migrations_current"] is True
    assert payload["expected_head"] is None
    assert payload["expected_heads"] == []
    assert payload["migrations_check"] == "skipped_no_alembic_files"


def test_readyz_jobs_heartbeat_missing(monkeypatch, client, async_session_maker):
    settings.job_heartbeat_required = True
    monkeypatch.setattr(routes_health, "_load_expected_heads", lambda: ([], "skipped_no_alembic_files"))

    response = client.get("/readyz")

    assert response.status_code == 503
    jobs = response.json()["jobs"]
    assert jobs["enabled"] is True
    assert jobs["ok"] is False


def test_readyz_jobs_heartbeat_recent(monkeypatch, client, async_session_maker):
    settings.job_heartbeat_required = True
    settings.job_heartbeat_ttl_seconds = 300
    monkeypatch.setattr(routes_health, "_load_expected_heads", lambda: ([], "skipped_no_alembic_files"))
    asyncio.run(_set_job_heartbeat(async_session_maker, age_seconds=30))

    response = client.get("/readyz")

    assert response.status_code == 200
    jobs = response.json()["jobs"]
    assert jobs["enabled"] is True
    assert jobs["ok"] is True
