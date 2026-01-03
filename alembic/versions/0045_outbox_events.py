"""Outbox events table

Revision ID: 0045_outbox_events
Revises: 0044_postgres_rls_org_isolation
Create Date: 2025-01-01
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0045_outbox_events"
down_revision = "0044_postgres_rls_org_isolation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outbox_events",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("ix_outbox_org_status", "outbox_events", ["org_id", "status", "next_attempt_at"])
    op.create_index("ix_outbox_org_dedupe", "outbox_events", ["org_id", "dedupe_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_outbox_org_dedupe", table_name="outbox_events")
    op.drop_index("ix_outbox_org_status", table_name="outbox_events")
    op.drop_table("outbox_events")
