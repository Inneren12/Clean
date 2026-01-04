from __future__ import annotations
import contextlib
import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.settings import settings


CORE_TABLES = {
    "teams",
    "bookings",
    "leads",
    "invoices",
    "invoice_payments",
    "workers",
    "documents",
    "order_photos",
    "subscriptions",
    "disputes",
    "financial_adjustment_events",
    "admin_audit_logs",
    "export_events",
    "email_events",
}


@contextlib.contextmanager
def _temporary_postgres_database(base_url: str):
    url = make_url(base_url)
    if url.get_backend_name() != "postgresql":
        pytest.skip("Migration invariants require a PostgreSQL DATABASE_URL")

    database_name = f"migration_check_{uuid.uuid4().hex}"
    admin_url = url.set(database=url.database or "postgres")
    admin_engine = sa.create_engine(
        admin_url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 5}
    )

    try:
        with admin_engine.connect() as conn:
            conn.execute(sa.text(f'CREATE DATABASE "{database_name}"'))
    except sa.exc.OperationalError as exc:  # pragma: no cover - env specific
        pytest.skip(f"PostgreSQL unavailable for migration invariants: {exc}")

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


def _assert_unique_constraint(inspector: sa.Inspector, table: str, name: str, columns: set[str]):
    constraints = inspector.get_unique_constraints(table)
    assert any(
        constraint.get("name") == name and set(constraint.get("column_names", ())) == columns
        for constraint in constraints
    ), f"Expected unique constraint {name} on {table}"


@pytest.mark.postgres
@pytest.mark.migrations
def test_postgres_migration_invariants():
    config = Config("alembic.ini")
    original_url = settings.database_url

    with _temporary_postgres_database(original_url) as temp_url:
        try:
            settings.database_url = temp_url
            config.set_main_option("sqlalchemy.url", temp_url)
            command.upgrade(config, "head")
        finally:
            settings.database_url = original_url

        engine = sa.create_engine(temp_url)
        inspector = sa.inspect(engine)

        for table in CORE_TABLES:
            columns = {column["name"]: column for column in inspector.get_columns(table)}
            assert "org_id" in columns, f"org_id missing on {table}"
            assert columns["org_id"].get("nullable") is False, f"org_id should be NOT NULL on {table}"

            fk_defs = inspector.get_foreign_keys(table)
            assert any(
                set(fk.get("constrained_columns", ())) == {"org_id"}
                and fk.get("referred_table") == "organizations"
                and set(fk.get("referred_columns", ())) == {"org_id"}
                for fk in fk_defs
            ), f"org_id on {table} should reference organizations.org_id"

        stripe_columns = {column["name"] for column in inspector.get_columns("stripe_events")}
        assert "org_id" in stripe_columns, "stripe_events.org_id should exist"

        stripe_fk_defs = inspector.get_foreign_keys("stripe_events")
        assert any(
            set(fk.get("constrained_columns", ())) == {"org_id"}
            and fk.get("referred_table") == "organizations"
            and set(fk.get("referred_columns", ())) == {"org_id"}
            for fk in stripe_fk_defs
        ), "stripe_events.org_id should reference organizations.org_id"

        _assert_unique_constraint(
            inspector, "email_events", "uq_email_events_org_dedupe", {"org_id", "dedupe_key"}
        )
        _assert_unique_constraint(
            inspector, "unsubscribe", "uq_unsubscribe_recipient_scope", {"org_id", "recipient", "scope"}
        )

        engine.dispose()
