import asyncio
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import anyio
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.domain.analytics import db_models as analytics_db_models  # noqa: F401
from app.domain.bookings import db_models as booking_db_models  # noqa: F401
from app.domain.export_events import db_models as export_events_db_models  # noqa: F401
from app.domain.leads import db_models  # noqa: F401
from app.domain.invoices import db_models as invoice_db_models  # noqa: F401
from app.infra.bot_store import InMemoryBotStore
from app.infra.db import Base, get_db_session
from app.main import app
from app.settings import settings


@pytest.fixture(scope="session")
def test_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    async def init_models() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(sa.insert(booking_db_models.Team).values(team_id=1, name="Default Team"))

    asyncio.run(init_models())
    yield engine
    asyncio.run(engine.dispose())


@pytest.fixture(scope="session")
def async_session_maker(test_engine):
    return async_sessionmaker(test_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def restore_admin_settings():
    original_username = settings.admin_basic_username
    original_password = settings.admin_basic_password
    original_dispatcher_username = settings.dispatcher_basic_username
    original_dispatcher_password = settings.dispatcher_basic_password
    yield
    settings.admin_basic_username = original_username
    settings.admin_basic_password = original_password
    settings.dispatcher_basic_username = original_dispatcher_username
    settings.dispatcher_basic_password = original_dispatcher_password


@pytest.fixture(autouse=True)
def clean_database(test_engine):
    async def truncate_tables() -> None:
        async with test_engine.begin() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                await conn.execute(table.delete())
            await conn.execute(sa.insert(booking_db_models.Team).values(team_id=1, name="Default Team"))

    asyncio.run(truncate_tables())
    rate_limiter = getattr(app.state, "rate_limiter", None)
    reset = getattr(rate_limiter, "reset", None) if rate_limiter else None
    if reset:
        if inspect.iscoroutinefunction(reset):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(reset())
            else:
                anyio.from_thread.run(reset)
        else:
            reset()
    yield


@pytest.fixture()
def client(async_session_maker):
    async def override_db_session():
        async with async_session_maker() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    app.state.bot_store = InMemoryBotStore()
    app.state.db_session_factory = async_session_maker
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
