"""Admin idempotency table

Revision ID: 0046_admin_idempotency
Revises: 0045_outbox_events
Create Date: 2026-02-01
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0046_admin_idempotency"
down_revision = "0045_outbox_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_idempotency",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("endpoint", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=128), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.org_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("org_id", "key", "endpoint", name="uq_admin_idempotency_key"),
    )
    op.create_index(
        "ix_admin_idempotency_org_endpoint", "admin_idempotency", ["org_id", "endpoint"], unique=False
    )
    op.create_index(
        "ix_admin_idempotency_org_hash", "admin_idempotency", ["org_id", "request_hash"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_admin_idempotency_org_hash", table_name="admin_idempotency")
    op.drop_index("ix_admin_idempotency_org_endpoint", table_name="admin_idempotency")
    op.drop_table("admin_idempotency")
