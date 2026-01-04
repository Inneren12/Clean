"""add pause metadata to organization billing

Revision ID: 1a6b6e3f2c2c
Revises: a2cce6391ad9
Create Date: 2025-02-20 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "1a6b6e3f2c2c"
down_revision = "a2cce6391ad9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organization_billing",
        sa.Column("pause_reason_code", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "organization_billing",
        sa.Column("resume_reason_code", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "organization_billing",
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "organization_billing",
        sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organization_billing", "resumed_at")
    op.drop_column("organization_billing", "paused_at")
    op.drop_column("organization_billing", "resume_reason_code")
    op.drop_column("organization_billing", "pause_reason_code")
