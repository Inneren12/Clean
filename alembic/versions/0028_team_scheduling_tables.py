"""team working hours and blackouts tables

Revision ID: 0028_team_scheduling_tables
Revises: 0027_workers_and_assignments
Create Date: 2025-02-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0028_team_scheduling_tables"
down_revision = "0027_workers_and_assignments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "team_working_hours",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.team_id"), nullable=False),
        sa.Column("day_of_week", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.Time(timezone=False), nullable=False),
        sa.Column("end_time", sa.Time(timezone=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("team_id", "day_of_week", name="uq_team_day"),
    )
    op.create_index("ix_team_working_hours_team_id", "team_working_hours", ["team_id"])

    op.create_table(
        "team_blackouts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.team_id"), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_team_blackouts_team_id", "team_blackouts", ["team_id"])
    op.create_index(
        "ix_team_blackouts_team_start", "team_blackouts", ["team_id", "starts_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_team_blackouts_team_start", table_name="team_blackouts")
    op.drop_index("ix_team_blackouts_team_id", table_name="team_blackouts")
    op.drop_table("team_blackouts")

    op.drop_index("ix_team_working_hours_team_id", table_name="team_working_hours")
    op.drop_table("team_working_hours")
