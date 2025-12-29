"""admin audit logs

Revision ID: 0025_admin_audit_logs
Revises: 0024_disputes_schema
Create Date: 2025-01-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0025_admin_audit_logs"
down_revision = "0024_disputes_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_audit_logs",
        sa.Column("audit_id", sa.String(length=36), primary_key=True),
        sa.Column("action", sa.String(length=150), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("resource_type", sa.String(length=100), nullable=True),
        sa.Column("resource_id", sa.String(length=100), nullable=True),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_admin_audit_logs_resource_id", "admin_audit_logs", ["resource_id"])


def downgrade() -> None:
    op.drop_index("ix_admin_audit_logs_resource_id", table_name="admin_audit_logs")
    op.drop_table("admin_audit_logs")
