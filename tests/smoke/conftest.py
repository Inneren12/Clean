import asyncio
from datetime import time
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.bookings import db_models as booking_db_models
from app.domain.bookings.service import WORK_END_HOUR, WORK_START_HOUR
from app.domain.saas import db_models as saas_db_models
from app.infra.db import Base
from app.settings import settings

DEFAULT_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


async def _ensure_connection(engine) -> None:
    async with engine.begin() as conn:
        await conn.execute(sa.text("SELECT 1"))


@pytest.fixture(scope="session")
def test_engine():
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    try:
        asyncio.get_event_loop().run_until_complete(_ensure_connection(engine))
    except Exception:
        pytest.skip("Postgres is required for smoke tests")
    yield engine
    asyncio.get_event_loop().run_until_complete(engine.dispose())


@pytest.fixture(scope="session")
def async_session_maker(test_engine):
    return async_sessionmaker(test_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def clean_database(test_engine):
    async def _reset() -> None:
        async with test_engine.begin() as conn:
            table_names = [table.name for table in Base.metadata.sorted_tables]
            if table_names:
                joined = ", ".join(f'"{name}"' for name in table_names)
                await conn.execute(sa.text(f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE"))
            await conn.execute(sa.insert(booking_db_models.Team).values(team_id=1, name="Default Team"))
            await conn.execute(
                sa.insert(saas_db_models.Organization).values(org_id=DEFAULT_ORG_ID, name="Default Org"),
            )
            await conn.execute(
                sa.insert(booking_db_models.TeamWorkingHours),
                [
                    {
                        "team_id": 1,
                        "day_of_week": day,
                        "start_time": time(hour=WORK_START_HOUR, minute=0),
                        "end_time": time(hour=WORK_END_HOUR, minute=0),
                    }
                    for day in range(7)
                ],
            )
    asyncio.get_event_loop().run_until_complete(_reset())
    yield


@pytest.fixture()
def client(async_session_maker):
    from fastapi.testclient import TestClient

    from app.infra.db import get_db_session
    from app.infra.bot_store import InMemoryBotStore
    from app.main import app

    async def override_db_session():
        async with async_session_maker() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    app.state.bot_store = InMemoryBotStore()
    original_factory = getattr(app.state, "db_session_factory", None)
    app.state.db_session_factory = async_session_maker
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    app.state.db_session_factory = original_factory
