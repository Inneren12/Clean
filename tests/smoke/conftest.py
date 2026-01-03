import asyncio
from datetime import time
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.bookings import db_models as booking_db_models
from app.domain.bookings.service import WORK_END_HOUR, WORK_START_HOUR
from app.domain.saas.service import ensure_default_org_and_team
from app.domain.saas import db_models as saas_db_models
from app.infra.db import Base
from app.infra.org_context import set_current_org_id
from app.settings import settings

DEFAULT_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


async def _ensure_connection(engine) -> None:
    async with engine.begin() as conn:
        await conn.execute(sa.text("SELECT 1"))


@pytest.fixture(scope="session")
def test_engine():
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        asyncio.run(_ensure_connection(engine))
    except Exception:
        pytest.skip("Postgres is required for smoke tests")
    yield engine
    asyncio.run(engine.dispose())


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
        seed_session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
        async with seed_session_factory() as session:
            await ensure_default_org_and_team(session)
            await session.execute(
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
            await session.commit()
    asyncio.run(_reset())
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


@pytest.fixture(autouse=True)
def _smoke_org_header_support():
    from app.main import app

    if not getattr(app.state, "smoke_org_header_middleware_added", False):

        @app.middleware("http")
        async def _inject_test_org(request, call_next):  # type: ignore[override]
            header_value = request.headers.get("X-Test-Org")
            if header_value:
                try:
                    request.state.current_org_id = uuid.UUID(header_value)
                    set_current_org_id(request.state.current_org_id)
                except Exception:  # noqa: BLE001
                    pass
            return await call_next(request)

        app.state.smoke_org_header_middleware_added = True

    yield
