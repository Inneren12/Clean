import asyncio

import pytest
from sqlalchemy import text

from app.api import routes_health


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
        routes_health, "_load_expected_heads", lambda: (None, "skipped_no_alembic_files")
    )

    response = client.get("/readyz")

    assert response.status_code == 200
    payload = response.json()["database"]
    assert payload["ok"] is True
    assert payload["migrations_current"] is True
    assert payload["expected_head"] is None
    assert payload["expected_heads"] == []
    assert payload["migrations_check"] == "skipped_no_alembic_files"


def test_readyz_alembic_error_should_503(monkeypatch, client, async_session_maker):
    asyncio.run(_set_alembic_version(async_session_maker, "any"))
    monkeypatch.setattr(routes_health, "_load_expected_heads", lambda: (None, "error_loading_alembic"))

    response = client.get("/readyz")

    assert response.status_code == 503
    payload = response.json()["database"]
    assert payload["migrations_current"] is False
    assert payload["migrations_check"] == "error_loading_alembic"
