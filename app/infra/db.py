import logging
from typing import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.exc import TimeoutError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from app.settings import settings

Base = declarative_base()

# Import models that use string-based relationship references to ensure they are registered
# when Base metadata is configured.
import app.infra.models  # noqa: F401,E402

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _engine, _session_factory
    if _session_factory is None:
        _engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout_seconds,
            connect_args={
                "options": f"-c statement_timeout={int(settings.database_statement_timeout_ms)}",
            },
        )
        _configure_logging(_engine)
        _session_factory = async_sessionmaker(
            _engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _session_factory


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    session_factory = _get_session_factory()
    try:
        async with session_factory() as session:
            yield session
    except TimeoutError as exc:
        import logging

        logging.getLogger(__name__).warning("db_pool_timeout", exc_info=exc)
        raise


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return _get_session_factory()


def _configure_logging(engine) -> None:
    @event.listens_for(engine.sync_engine, "connect")
    def set_statement_timeout(dbapi_connection, connection_record):  # noqa: ANN001
        try:
            with dbapi_connection.cursor() as cursor:
                cursor.execute(
                    "SET statement_timeout = %s",
                    (int(settings.database_statement_timeout_ms),),
                )
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).warning("db_statement_timeout_set_failed")

    @event.listens_for(engine.sync_engine, "handle_error")
    def receive_error(context):  # noqa: ANN001
        logger = logging.getLogger(__name__)
        if isinstance(context.original_exception, TimeoutError):
            logger.warning(
                "db_pool_timeout",
                extra={"extra": {"operation": str(context.statement) if context.statement else None}},
            )
