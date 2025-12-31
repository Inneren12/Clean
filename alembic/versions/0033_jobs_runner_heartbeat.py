"""
Revision ID: 0033_jobs_runner_heartbeat
Revises: 0032_saas_billing_and_limits
Create Date: 2025-05-31
"""

import sqlalchemy as sa
from alembic import op

revision = "0033_jobs_runner_heartbeat"
down_revision = "0032_saas_billing_and_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_heartbeats",
        sa.Column("name", sa.String(length=64), primary_key=True),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("job_heartbeats")
