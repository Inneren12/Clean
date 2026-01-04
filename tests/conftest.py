import asyncio
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def ensure_event_loop() -> asyncio.AbstractEventLoop:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop


ensure_event_loop()

import anyio
import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool
from datetime import time
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
import uuid

from app.domain.analytics import db_models as analytics_db_models  # noqa: F401
from app.domain.bookings import db_models as booking_db_models  # noqa: F401
from app.domain.bookings.service import WORK_END_HOUR, WORK_START_HOUR
from app.domain.addons import db_models as addon_db_models  # noqa: F401
from app.domain.export_events import db_models as export_events_db_models  # noqa: F401
from app.domain.leads import db_models  # noqa: F401
from app.domain.invoices import db_models as invoice_db_models  # noqa: F401
from app.domain.data_rights import db_models as data_rights_db_models  # noqa: F401
from app.domain.time_tracking import db_models as time_tracking_db_models  # noqa: F401
from app.domain.reason_logs import db_models as reason_logs_db_models  # noqa: F401
from app.domain.subscriptions import db_models as subscription_db_models  # noqa: F401
from app.domain.checklists import db_models as checklist_db_models  # noqa: F401
from app.domain.clients import db_models as client_db_models  # noqa: F401
from app.domain.nps import db_models as nps_db_models  # noqa: F401
from app.domain.disputes import db_models as dispute_db_models  # noqa: F401
from app.domain.policy_overrides import db_models as policy_override_db_models  # noqa: F401
from app.domain.admin_audit import db_models as admin_audit_db_models  # noqa: F401
from app.domain.admin_idempotency import db_models as admin_idempotency_db_models  # noqa: F401
from app.domain.documents import db_models as document_db_models  # noqa: F401
from app.domain.break_glass import db_models as break_glass_db_models  # noqa: F401
from app.domain.saas import db_models as saas_db_models  # noqa: F401
from app.domain.outbox import db_models as outbox_db_models  # noqa: F401
from app.domain.saas.service import ensure_default_org_and_team
from app.domain.ops import db_models as ops_db_models  # noqa: F401
from app.infra.bot_store import InMemoryBotStore
from app.infra.db import Base, get_db_session
from app.infra.org_context import set_current_org_id
from app.main import app
from app.settings import settings

DEFAULT_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture(scope="session")
def test_engine():
    db_path = Path("test.db")
    if db_path.exists():
        db_path.unlink()
    engine = create_async_engine(
        "sqlite+aiosqlite:///./test.db",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=StaticPool,
    )

    seed_session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def init_models() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

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
    original_email_mode = getattr(settings, "email_mode", "off")
    original_legacy_basic_auth_enabled = getattr(settings, "legacy_basic_auth_enabled", True)
    original_auth_secret_key = getattr(settings, "auth_secret_key", "")
    original_admin_mfa_required = getattr(settings, "admin_mfa_required", False)
    original_admin_mfa_roles = getattr(settings, "admin_mfa_required_roles_raw", None)
    original_admin_read_only = getattr(settings, "admin_read_only", False)
    original_admin_ip_allowlist = getattr(settings, "admin_ip_allowlist_cidrs_raw", None)
    original_trust_proxy_headers = getattr(settings, "trust_proxy_headers", False)
    original_trusted_proxy_ips = getattr(settings, "trusted_proxy_ips_raw", None)
    original_trusted_proxy_cidrs = getattr(settings, "trusted_proxy_cidrs_raw", None)
    original_dlq_auto_replay_enabled = getattr(settings, "dlq_auto_replay_enabled", False)
    original_dlq_outbox_kinds = getattr(settings, "dlq_auto_replay_allow_outbox_kinds_raw", None)
    original_dlq_export_modes = getattr(settings, "dlq_auto_replay_allow_export_modes_raw", None)
    original_dlq_min_age = getattr(settings, "dlq_auto_replay_min_age_minutes", 60)
    original_dlq_max_per_org = getattr(settings, "dlq_auto_replay_max_per_org", 5)
    original_dlq_failure_streak = getattr(settings, "dlq_auto_replay_failure_streak_limit", 3)
    original_dlq_outbox_attempt_ceiling = getattr(settings, "dlq_auto_replay_outbox_attempt_ceiling", 7)
    original_dlq_export_replay_limit = getattr(settings, "dlq_auto_replay_export_replay_limit", 2)
    original_dlq_export_cooldown = getattr(settings, "dlq_auto_replay_export_cooldown_minutes", 120)
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
    settings.email_mode = original_email_mode
    settings.admin_mfa_required = original_admin_mfa_required
    settings.admin_mfa_required_roles_raw = original_admin_mfa_roles
    settings.admin_read_only = original_admin_read_only
    settings.admin_ip_allowlist_cidrs_raw = original_admin_ip_allowlist
    settings.trust_proxy_headers = original_trust_proxy_headers
    settings.trusted_proxy_ips_raw = original_trusted_proxy_ips
    settings.trusted_proxy_cidrs_raw = original_trusted_proxy_cidrs
    settings.dlq_auto_replay_enabled = original_dlq_auto_replay_enabled
    settings.dlq_auto_replay_allow_outbox_kinds_raw = original_dlq_outbox_kinds
    settings.dlq_auto_replay_allow_export_modes_raw = original_dlq_export_modes
    settings.dlq_auto_replay_min_age_minutes = original_dlq_min_age
    settings.dlq_auto_replay_max_per_org = original_dlq_max_per_org
    settings.dlq_auto_replay_failure_streak_limit = original_dlq_failure_streak
    settings.dlq_auto_replay_outbox_attempt_ceiling = original_dlq_outbox_attempt_ceiling
    settings.dlq_auto_replay_export_replay_limit = original_dlq_export_replay_limit
    settings.dlq_auto_replay_export_cooldown_minutes = original_dlq_export_cooldown


@pytest.fixture(autouse=True)
def enable_test_mode():
    settings.testing = True
    settings.deposits_enabled = False
    settings.app_env = "dev"
    settings.email_mode = "sendgrid"
    from app.infra.email import resolve_email_adapter

    app.state.email_adapter = resolve_email_adapter(settings)
    app.state.storage_backend = None
    yield


@pytest.fixture(autouse=True)
def restore_app_state():
    """Restore app.state after each test to prevent state pollution."""
    original_metrics = getattr(app.state, "metrics", None)
    original_app_settings = getattr(app.state, "app_settings", None)
    yield
    # Restore original state
    if original_metrics is not None:
        app.state.metrics = original_metrics
    elif hasattr(app.state, "metrics"):
        delattr(app.state, "metrics")

    if original_app_settings is not None:
        app.state.app_settings = original_app_settings
    elif hasattr(app.state, "app_settings"):
        delattr(app.state, "app_settings")


@pytest.fixture(autouse=True)
def override_org_resolver(monkeypatch):
    if not getattr(app.state, "test_org_header_middleware_added", False):

        @app.middleware("http")
        async def _inject_test_org(request, call_next):  # type: ignore[override]
            header_value = request.headers.get("X-Test-Org")
            if header_value:
                try:
                    request.state.current_org_id = uuid.UUID(header_value)
                    set_current_org_id(request.state.current_org_id)
                except Exception as exc:  # noqa: BLE001
                    raise ValueError(f"Invalid X-Test-Org header: {header_value}") from exc
            return await call_next(request)

        app.state.test_org_header_middleware_added = True

    def _resolve_org_id(request):
        if request:
            state_org_id = getattr(request.state, "current_org_id", None)
            if state_org_id:
                try:
                    resolved = uuid.UUID(str(state_org_id))
                    set_current_org_id(resolved)
                    return resolved
                except Exception as exc:  # noqa: BLE001
                    raise ValueError(f"Invalid state org id: {state_org_id}") from exc

            header_value = request.headers.get("X-Test-Org")
            if header_value:
                try:
                    resolved = uuid.UUID(header_value)
                    set_current_org_id(resolved)
                    return resolved
                except Exception as exc:  # noqa: BLE001
                    raise ValueError(f"Invalid X-Test-Org header: {header_value}") from exc

        set_current_org_id(settings.default_org_id)
        return settings.default_org_id

    monkeypatch.setattr("app.api.entitlements.resolve_org_id", _resolve_org_id)
    yield


@pytest.fixture(autouse=True)
def clean_database(test_engine):
    async def truncate_tables() -> None:
        async with test_engine.begin() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                await conn.execute(table.delete())

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
    action_rate_limiter = getattr(app.state, "action_rate_limiter", None)
    reset_action = getattr(action_rate_limiter, "reset", None) if action_rate_limiter else None
    if reset_action:
        if inspect.iscoroutinefunction(reset_action):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(reset_action())
            else:
                anyio.from_thread.run(reset_action)
        else:
            reset_action()
    yield


@pytest.fixture()
def client(async_session_maker):
    ensure_event_loop()

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
