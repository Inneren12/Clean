"""
Add export_events dead-letter table

Revision ID: 0009_export_events_dead_letter
Revises: 0008_default_team_unique
Create Date: 2025-03-15 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_export_events_dead_letter"
down_revision = "0008_default_team_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "export_events",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("lead_id", sa.String(length=36), nullable=True),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("target_url_host", sa.String(length=255), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(
        "ix_export_events_created_lead",
        "export_events",
        ["created_at", "lead_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_export_events_created_lead", table_name="export_events")
    op.drop_table("export_events")
