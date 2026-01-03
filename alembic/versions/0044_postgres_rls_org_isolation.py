"""add org isolation rls

Revision ID: 0044_postgres_rls_org_isolation
Revises: 0043_subscription_status_reason
Create Date: 2025-06-15 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0044_postgres_rls_org_isolation"
down_revision = "0043_subscription_status_reason"
branch_labels = None
depends_on = None


TABLES = (
    "leads",
    "bookings",
    "invoices",
    "invoice_payments",
    "workers",
    "teams",
    "order_photos",
    "export_events",
    "email_events",
)


def _is_postgres() -> bool:
    bind = op.get_bind()
    return bind.dialect.name == "postgresql"


def _policy_sql(table: str) -> str:
    return f"""
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS {table}_org_isolation ON {table};
CREATE POLICY {table}_org_isolation ON {table}
    USING (org_id = current_setting('app.current_org_id', true)::uuid)
    WITH CHECK (org_id = current_setting('app.current_org_id', true)::uuid);
"""


def _downgrade_sql(table: str) -> str:
    return f"""
DROP POLICY IF EXISTS {table}_org_isolation ON {table};
ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;
ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;
"""


def upgrade() -> None:
    if not _is_postgres():
        return

    for table in TABLES:
        op.execute(sa.text(_policy_sql(table)))


def downgrade() -> None:
    if not _is_postgres():
        return

    for table in TABLES:
        op.execute(sa.text(_downgrade_sql(table)))
