import sys
from logging.config import fileConfig
from pathlib import Path
from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.domain.bookings import db_models as booking_db_models  # noqa: F401
from app.domain.addons import db_models as addon_db_models  # noqa: F401
from app.domain.checklists import db_models as checklist_db_models  # noqa: F401
from app.domain.export_events import db_models as export_events_db_models  # noqa: F401
from app.domain.leads import db_models  # noqa: F401
from app.domain.invoices import db_models as invoice_db_models  # noqa: F401
from app.domain.reason_logs import db_models as reason_logs_db_models  # noqa: F401
from app.domain.time_tracking import db_models as time_tracking_db_models  # noqa: F401
from app.domain.nps import db_models as nps_db_models  # noqa: F401
from app.domain.clients import db_models as client_db_models  # noqa: F401
from app.domain.subscriptions import db_models as subscription_db_models  # noqa: F401
from app.infra.db import Base
from app.settings import settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _sync_database_url(raw_url: str) -> str:
    url = make_url(raw_url)
    if url.drivername.endswith("+aiosqlite"):
        url = url.set(drivername="sqlite")
    return url.render_as_string(hide_password=False)


config.set_main_option("sqlalchemy.url", _sync_database_url(settings.database_url))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
