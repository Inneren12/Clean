"""
Ensure org_id columns use UUID and seed deterministic default org.

Revision ID: 0034_org_id_uuid_and_default_org
Revises: 0033_jobs_runner_heartbeat
Create Date: 2025-05-15
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = "0034_org_id_uuid_and_default_org"
down_revision = "0033_jobs_runner_heartbeat"
branch_labels = None
depends_on = None

UUID_TYPE = sa.Uuid(as_uuid=True)
DEFAULT_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
DEFAULT_ORG_NAME = "Default Org"


def _column_is_uuid(conn, table: str) -> bool:
    result = conn.execute(
        sa.text(
            """
            SELECT data_type
            FROM information_schema.columns
            WHERE table_name = :table
              AND column_name = 'org_id'
            """
        ),
        {"table": table},
    )
    return (result.scalar() or "").lower() == "uuid"


def _validate_uuid_values(conn, table: str) -> None:
    invalid = conn.execute(
        sa.text(
            f"""
            SELECT org_id
            FROM {table}
            WHERE org_id IS NOT NULL
              AND org_id::text !~* '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$'
            LIMIT 1
            """
        )
    ).fetchone()
    if invalid:
        raise RuntimeError(f"Invalid org_id value in {table}: {invalid[0]}")


def _get_org_fk_names(inspector: sa.inspection.Inspector, table: str) -> list[str]:
    fks = []
    for fk in inspector.get_foreign_keys(table):
        if fk.get("referred_table") == "organizations" and fk.get("constrained_columns") == ["org_id"]:
            if fk.get("name"):
                fks.append(fk["name"])
    return fks


def _create_org_fk(table: str) -> None:
    op.create_foreign_key(
        f"fk_{table}_org_id_organizations",
        table,
        "organizations",
        ["org_id"],
        ["org_id"],
        ondelete="CASCADE",
    )


def _convert_org_id_column(table: str, inspector: sa.inspection.Inspector) -> bool:
    conn = op.get_bind()
    if getattr(conn.engine.dialect, "name", "") != "postgresql":
        return False
    _validate_uuid_values(conn, table)
    column_is_uuid = _column_is_uuid(conn, table)
    if column_is_uuid:
        return False

    fk_names = _get_org_fk_names(inspector, table)
    for fk in fk_names:
        op.drop_constraint(fk, table_name=table, type_="foreignkey")

    op.alter_column(
        table,
        "org_id",
        type_=UUID_TYPE,
        existing_type=sa.String(length=36),
        postgresql_using="org_id::uuid",
    )
    return True


def _ensure_organizations_uuid(inspector: sa.inspection.Inspector) -> bool:
    conn = op.get_bind()
    if getattr(conn.engine.dialect, "name", "") != "postgresql":
        return False
    column_is_uuid = _column_is_uuid(conn, "organizations")
    if column_is_uuid:
        return False

    op.alter_column(
        "organizations",
        "org_id",
        type_=UUID_TYPE,
        existing_type=sa.String(length=36),
        postgresql_using="org_id::uuid",
    )
    return True


def _ensure_default_org() -> None:
    conn = op.get_bind()
    default_id = str(DEFAULT_ORG_ID)
    existing = conn.execute(
        sa.text("SELECT org_id, name FROM organizations WHERE org_id = :org_id"),
        {"org_id": default_id},
    ).fetchone()
    if existing:
        return

    # Avoid name conflicts with previously seeded default organizations
    name_conflict = conn.execute(
        sa.text(
            "SELECT org_id FROM organizations WHERE name = :name AND org_id <> :org_id LIMIT 1"
        ),
        {"name": DEFAULT_ORG_NAME, "org_id": default_id},
    ).fetchone()
    if name_conflict:
        conn.execute(
            sa.text("UPDATE organizations SET name = :new_name WHERE org_id = :org_id"),
            {"new_name": f"{DEFAULT_ORG_NAME} (legacy)", "org_id": name_conflict[0]},
        )

    conn.execute(
        sa.text(
            "INSERT INTO organizations (org_id, name) VALUES (:org_id, :name) ON CONFLICT (org_id) DO NOTHING"
        ),
        {"org_id": default_id, "name": DEFAULT_ORG_NAME},
    )

    conn.execute(
        sa.text(
            """
            INSERT INTO organization_billing (org_id, plan_id, status)
            VALUES (:org_id, 'free', 'inactive')
            ON CONFLICT (org_id) DO NOTHING
            """
        ),
        {"org_id": default_id},
    )


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = [
        "memberships",
        "api_tokens",
        "organization_billing",
        "organization_usage_events",
    ]

    fk_dropped: dict[str, list[str]] = {}

    if getattr(conn.engine.dialect, "name", "") == "postgresql":
        if not _column_is_uuid(conn, "organizations"):
            for table in tables:
                fk_names = _get_org_fk_names(inspector, table)
                if fk_names:
                    fk_dropped[table] = fk_names
                    for fk in fk_names:
                        op.drop_constraint(fk, table_name=table, type_="foreignkey")

            _ensure_organizations_uuid(inspector)

        for table in tables:
            changed = _convert_org_id_column(table, inspector)
            if changed:
                fk_dropped.setdefault(table, [])

        inspector = sa.inspect(conn)
        for table in tables:
            if table in fk_dropped or (
                _column_is_uuid(conn, table) and not _get_org_fk_names(inspector, table)
            ):
                _create_org_fk(table)

    _ensure_default_org()


def downgrade() -> None:
    # Type reversions are not safe automatically; keep UUID columns.
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM organizations WHERE org_id = :org_id AND name = :name"  # pragma: no cover
        ),
        {"org_id": str(DEFAULT_ORG_ID), "name": DEFAULT_ORG_NAME},
    )
