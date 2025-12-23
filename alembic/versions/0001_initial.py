"""initial

Revision ID: 0001_initial
Revises: 
Create Date: 2025-01-01 00:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("session_id", sa.String(length=36), primary_key=True),
        sa.Column("brand", sa.String(length=32), nullable=False),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            # ORM-managed updated_at (no database trigger).
            nullable=False,
        ),
    )
    op.create_table(
        "leads",
        sa.Column("lead_id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=255)),
        sa.Column("postal_code", sa.String(length=32)),
        sa.Column("address", sa.String(length=255)),
        sa.Column("preferred_dates", sa.JSON(), nullable=False),
        sa.Column("access_notes", sa.String(length=255)),
        sa.Column("parking", sa.String(length=255)),
        sa.Column("pets", sa.String(length=255)),
        sa.Column("allergies", sa.String(length=255)),
        sa.Column("notes", sa.String(length=500)),
        sa.Column("structured_inputs", sa.JSON(), nullable=False),
        sa.Column("estimate_snapshot", sa.JSON(), nullable=False),
        sa.Column("pricing_config_version", sa.String(length=32), nullable=False),
        sa.Column("config_hash", sa.String(length=128), nullable=False),
        sa.Column("utm_source", sa.String(length=100)),
        sa.Column("utm_medium", sa.String(length=100)),
        sa.Column("utm_campaign", sa.String(length=100)),
        sa.Column("utm_term", sa.String(length=100)),
        sa.Column("utm_content", sa.String(length=100)),
        sa.Column("referrer", sa.String(length=255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            # ORM-managed updated_at (no database trigger).
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("leads")
    op.drop_table("chat_sessions")
