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
from datetime import time
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
import uuid

from app.domain.analytics import db_models as analytics_db_models  # noqa: F401
from app.domain.bookings import db_models as booking_db_models  # noqa: F401
from app.domain.bookings.service import WORK_END_HOUR, WORK_START_HOUR
from app.domain.addons import db_models as addon_db_models  # noqa: F401
from app.domain.export_events import db_models as export_events_db_models  # noqa: F401
from app.domain.leads import db_models  # noqa: F401
from app.domain.invoices import db_models as invoice_db_models  # noqa: F401
from app.domain.time_tracking import db_models as time_tracking_db_models  # noqa: F401
from app.domain.reason_logs import db_models as reason_logs_db_models  # noqa: F401
from app.domain.subscriptions import db_models as subscription_db_models  # noqa: F401
from app.domain.checklists import db_models as checklist_db_models  # noqa: F401
from app.domain.clients import db_models as client_db_models  # noqa: F401
from app.domain.nps import db_models as nps_db_models  # noqa: F401
from app.domain.disputes import db_models as dispute_db_models  # noqa: F401
from app.domain.policy_overrides import db_models as policy_override_db_models  # noqa: F401
from app.domain.admin_audit import db_models as admin_audit_db_models  # noqa: F401
from app.domain.documents import db_models as document_db_models  # noqa: F401
from app.domain.saas import db_models as saas_db_models  # noqa: F401
from app.domain.ops import db_models as ops_db_models  # noqa: F401
from app.infra.bot_store import InMemoryBotStore
from app.infra.db import Base, get_db_session
from app.main import app
from app.settings import settings

DEFAULT_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


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
            await conn.execute(
                sa.insert(saas_db_models.Organization).values(org_id=DEFAULT_ORG_ID, name="Default Org")
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
    original_testing = getattr(settings, "testing", False)
    original_deposits = getattr(settings, "deposits_enabled", True)
    original_metrics = getattr(settings, "metrics_enabled", True)
    original_metrics_token = getattr(settings, "metrics_token", None)
    original_job_heartbeat = getattr(settings, "job_heartbeat_required", False)
    original_job_heartbeat_ttl = getattr(settings, "job_heartbeat_ttl_seconds", 300)
    original_legacy_basic_auth_enabled = getattr(settings, "legacy_basic_auth_enabled", True)
    original_auth_secret_key = getattr(settings, "auth_secret_key", "")
    yield
    settings.admin_basic_username = original_username
    settings.admin_basic_password = original_password
    settings.dispatcher_basic_username = original_dispatcher_username
    settings.dispatcher_basic_password = original_dispatcher_password
    settings.testing = original_testing
    settings.deposits_enabled = original_deposits
    settings.metrics_enabled = original_metrics
    settings.metrics_token = original_metrics_token
    settings.job_heartbeat_required = original_job_heartbeat
    settings.job_heartbeat_ttl_seconds = original_job_heartbeat_ttl
    settings.legacy_basic_auth_enabled = original_legacy_basic_auth_enabled
    settings.auth_secret_key = original_auth_secret_key


@pytest.fixture(autouse=True)
def enable_test_mode():
    settings.testing = True
    settings.deposits_enabled = False
    settings.app_env = "dev"
    from app.infra.email import resolve_email_adapter

    app.state.email_adapter = resolve_email_adapter(settings)
    app.state.storage_backend = None
    yield


@pytest.fixture(autouse=True)
def clean_database(test_engine):
    async def truncate_tables() -> None:
        async with test_engine.begin() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                await conn.execute(table.delete())
            await conn.execute(sa.insert(booking_db_models.Team).values(team_id=1, name="Default Team"))
            await conn.execute(
                sa.insert(saas_db_models.Organization).values(org_id=DEFAULT_ORG_ID, name="Default Org")
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
    original_factory = getattr(app.state, "db_session_factory", None)
    app.state.db_session_factory = async_session_maker
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    app.state.db_session_factory = original_factory


@pytest.fixture()
def client_no_raise(async_session_maker):
    """Test client that returns HTTP responses instead of raising server exceptions."""

    async def override_db_session():
        async with async_session_maker() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    app.state.bot_store = InMemoryBotStore()
    original_factory = getattr(app.state, "db_session_factory", None)
    app.state.db_session_factory = async_session_maker
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    app.state.db_session_factory = original_factory
