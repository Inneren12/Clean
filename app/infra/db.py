import logging
from typing import Any, AsyncGenerator

from fastapi import Request
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.exc import TimeoutError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from app.settings import settings
from app.infra.org_context import get_current_org_id, set_current_org_id

# Shared type definition to avoid circular imports - MUST be defined BEFORE Base and models
UUID_TYPE = sa.Uuid(as_uuid=True)

Base = declarative_base()

# Import models that use string-based relationship references to ensure they are registered
# when Base metadata is configured.
import app.infra.models  # noqa: F401,E402

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None

logger = logging.getLogger(__name__)


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _engine, _session_factory
    if _session_factory is None:
        is_postgres = settings.database_url.startswith(("postgresql://", "postgresql+"))

        engine_kwargs: dict[str, Any] = {
            "pool_pre_ping": True,
        }

        if is_postgres:
            # Apply Postgres-specific pool settings
            engine_kwargs.update({
                "pool_size": settings.database_pool_size,
                "max_overflow": settings.database_max_overflow,
                "pool_timeout": settings.database_pool_timeout_seconds,
                "connect_args": {
                    "options": f"-c statement_timeout={int(settings.database_statement_timeout_ms)}",
                },
            })

        _engine = create_async_engine(settings.database_url, **engine_kwargs)
        _configure_logging(_engine, is_postgres)
        _configure_org_context(_engine, is_postgres)
        _session_factory = async_sessionmaker(
            _engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _session_factory


async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    session_factory = _get_session_factory()
    org_id = getattr(request.state, "current_org_id", None) or settings.default_org_id
    set_current_org_id(org_id)
    try:
        async with session_factory() as session:
            yield session
    except TimeoutError as exc:
        logger.warning("db_pool_timeout", exc_info=exc)
        raise


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return _get_session_factory()


def _configure_logging(engine, is_postgres: bool) -> None:
    @event.listens_for(engine.sync_engine, "handle_error")
    def receive_error(context):  # noqa: ANN001
        # Check both original_exception and sqlalchemy_exception for timeout
        exc = context.original_exception or context.sqlalchemy_exception
        if isinstance(exc, TimeoutError):
            logger.warning(
                "db_pool_timeout",
                extra={"extra": {"operation": str(context.statement) if context.statement else None}},
            )


def _configure_org_context(engine, is_postgres: bool) -> None:
    if not is_postgres:
        return

    @event.listens_for(engine.sync_engine, "begin")
    def set_org_id_on_begin(conn):  # noqa: ANN001
        org_id = get_current_org_id()
        if org_id is None:
            return

        conn.exec_driver_sql("SET LOCAL app.current_org_id = %s", (str(org_id),))
