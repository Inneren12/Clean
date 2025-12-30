"""workers and booking assignment

Revision ID: 0027_workers_and_assignments
Revises: 0026_documents
Create Date: 2025-01-01 00:00:00.000001
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0027_workers_and_assignments"
down_revision = "0026_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workers",
        sa.Column("worker_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.team_id"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("phone", sa.String(length=50), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("role", sa.String(length=80), nullable=True),
        sa.Column("hourly_rate_cents", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_workers_team", "workers", ["team_id"])

    with op.batch_alter_table("bookings") as batch_op:
        batch_op.add_column(sa.Column("assigned_worker_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_bookings_assigned_worker",
            "workers",
            ["assigned_worker_id"],
            ["worker_id"],
        )
        batch_op.create_index("ix_bookings_assigned_worker_id", ["assigned_worker_id"])


def downgrade() -> None:
    with op.batch_alter_table("bookings") as batch_op:
        batch_op.drop_index("ix_bookings_assigned_worker_id")
        batch_op.drop_constraint("fk_bookings_assigned_worker", type_="foreignkey")
        batch_op.drop_column("assigned_worker_id")

    op.drop_index("ix_workers_team", table_name="workers")
    op.drop_table("workers")
