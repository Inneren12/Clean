"""slots v1 tables

Revision ID: 0002_slots_v1
Revises: 0001_initial
Create Date: 2025-02-01 00:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_slots_v1"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("team_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    team_table = sa.table("teams", sa.column("team_id", sa.Integer()), sa.column("name", sa.String()))
    op.bulk_insert(team_table, [{"name": "Default Team"}])

    op.create_table(
        "bookings",
        sa.Column("booking_id", sa.String(length=36), primary_key=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.team_id"), nullable=False),
        sa.Column("lead_id", sa.String(length=36), sa.ForeignKey("leads.lead_id"), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.team_id"], name="fk_booking_team"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.lead_id"], name="fk_booking_lead"),
    )
    op.create_index("ix_bookings_starts_status", "bookings", ["starts_at", "status"])
    op.create_index("ix_bookings_status", "bookings", ["status"])


def downgrade() -> None:
    op.drop_index("ix_bookings_status", table_name="bookings")
    op.drop_index("ix_bookings_starts_status", table_name="bookings")
    op.drop_table("bookings")
    op.drop_table("teams")
