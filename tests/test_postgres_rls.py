import contextlib
import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.settings import settings


@contextlib.contextmanager
def _temporary_postgres_database(base_url: str):
    url = make_url(base_url)
    if url.get_backend_name() != "postgresql":
        pytest.skip("Row-level security checks require PostgreSQL")

    database_name = f"rls_check_{uuid.uuid4().hex}"
    admin_url = url.set(database=url.database or "postgres")
    admin_engine = sa.create_engine(
        admin_url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 5}
    )

    try:
        with admin_engine.connect() as conn:
            conn.execute(sa.text(f'CREATE DATABASE "{database_name}"'))
    except sa.exc.OperationalError as exc:  # pragma: no cover - env specific
        pytest.skip(f"PostgreSQL unavailable for RLS check: {exc}")

    try:
        yield url.set(database=database_name).render_as_string(hide_password=False)
    finally:
        with admin_engine.connect() as conn:
            conn.execute(
                sa.text(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = :db_name AND pid <> pg_backend_pid()
                    """
                ),
                {"db_name": database_name},
            )
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        admin_engine.dispose()


def _apply_migrations(temp_url: str) -> None:
    config = Config("alembic.ini")
    original_url = settings.database_url

    try:
        settings.database_url = temp_url
        config.set_main_option("sqlalchemy.url", temp_url)
        command.upgrade(config, "head")
    finally:
        settings.database_url = original_url


def _provision_tenant_engine(temp_url: str) -> sa.Engine:
    base_url = make_url(temp_url)
    admin_engine = sa.create_engine(base_url, future=True)

    with admin_engine.begin() as conn:
        conn.execute(sa.text("DROP ROLE IF EXISTS app_test"))
        conn.execute(
            sa.text(
                "CREATE ROLE app_test LOGIN PASSWORD 'app_test' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
            )
        )
        conn.execute(sa.text("GRANT USAGE ON SCHEMA public TO app_test"))
        conn.execute(sa.text("GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA public TO app_test"))
        conn.execute(sa.text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_test"))
        conn.execute(
            sa.text(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT ON TABLES TO app_test"
            )
        )

    admin_engine.dispose()

    tenant_url = base_url.set(username="app_test", password="app_test").render_as_string(
        hide_password=False
    )
    return sa.create_engine(tenant_url, future=True)


@pytest.mark.postgres
@pytest.mark.migrations
def test_rls_prevents_cross_org_queries():
    with _temporary_postgres_database(settings.database_url) as temp_url:
        _apply_migrations(temp_url)
        engine = _provision_tenant_engine(temp_url)
        org_a = uuid.uuid4()
        org_b = uuid.uuid4()

        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO organizations (org_id, name) VALUES (:org_id, :name), (:org_b, :name_b)"
                ),
                {
                    "org_id": org_a,
                    "name": "RLS Org A",
                    "org_b": org_b,
                    "name_b": "RLS Org B",
                },
            )

        with engine.begin() as conn:
            conn.execute(sa.text(f"SET LOCAL app.current_org_id = '{org_a}'"))
            conn.execute(
                sa.text("INSERT INTO teams (org_id, name) VALUES (:org_id, :name)"),
                {"org_id": org_a, "name": "Team A"},
            )

        with engine.begin() as conn:
            conn.execute(sa.text(f"SET LOCAL app.current_org_id = '{org_b}'"))
            conn.execute(
                sa.text("INSERT INTO teams (org_id, name) VALUES (:org_id, :name)"),
                {"org_id": org_b, "name": "Team B"},
            )

        with engine.begin() as conn:
            rows = conn.execute(sa.text("SELECT org_id, name FROM teams ORDER BY name"))
            assert rows.fetchall() == []

        with engine.begin() as conn:
            conn.execute(sa.text(f"SET LOCAL app.current_org_id = '{org_a}'"))
            rows = conn.execute(sa.text("SELECT org_id, name FROM teams ORDER BY name"))
            assert {row.org_id for row in rows} == {org_a}

        engine.dispose()


@pytest.mark.postgres
@pytest.mark.migrations
def test_rls_rejects_writes_without_org_context():
    with _temporary_postgres_database(settings.database_url) as temp_url:
        _apply_migrations(temp_url)
        engine = _provision_tenant_engine(temp_url)

        org_id = uuid.uuid4()
        lead_id = uuid.uuid4()

        with engine.begin() as conn:
            conn.execute(
                sa.text("INSERT INTO organizations (org_id, name) VALUES (:org_id, :name)"),
                {"org_id": org_id, "name": "RLS Org"},
            )

        with pytest.raises(sa.exc.DatabaseError):
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "INSERT INTO leads (lead_id, org_id, name) VALUES (:lead_id, :org_id, :name)"
                    ),
                    {"lead_id": lead_id, "org_id": org_id, "name": "Blocked lead"},
                )

        with engine.begin() as conn:
            conn.execute(sa.text(f"SET LOCAL app.current_org_id = '{org_id}'"))
            conn.execute(
                sa.text(
                    "INSERT INTO leads (lead_id, org_id, name) VALUES (:lead_id, :org_id, :name)"
                ),
                {"lead_id": lead_id, "org_id": org_id, "name": "Allowed lead"},
            )
            rows = conn.execute(sa.text("SELECT lead_id FROM leads"))
            assert {row.lead_id for row in rows} == {lead_id}

        engine.dispose()
