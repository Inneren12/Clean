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


def _ensure_uuid_column(table: str) -> None:
    conn = op.get_bind()
    if getattr(conn.engine.dialect, "name", "") == "sqlite":
        return
    _validate_uuid_values(conn, table)
    column_is_uuid = _column_is_uuid(conn, table)
    with op.batch_alter_table(table, recreate_fks=True) as batch_op:
        batch_op.alter_column(
            "org_id",
            type_=UUID_TYPE,
            existing_type=UUID_TYPE if column_is_uuid else sa.String(length=36),
            postgresql_using=None if column_is_uuid else "org_id::uuid",
        )


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
    _ensure_uuid_column("organization_billing")
    _ensure_uuid_column("organization_usage_events")
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
