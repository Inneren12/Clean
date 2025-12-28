"""time tracking tables and planned minutes

Revision ID: 0013_time_tracking
Revises: 0012_stripe_invoice_payments
Create Date: 2024-06-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0013_time_tracking"
down_revision = "0012_stripe_invoice_payments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bookings", sa.Column("planned_minutes", sa.Integer(), nullable=True))
    op.add_column("bookings", sa.Column("actual_seconds", sa.Integer(), nullable=True))

    op.execute("UPDATE bookings SET planned_minutes = duration_minutes WHERE planned_minutes IS NULL")

    op.create_table(
        "work_time_entries",
        sa.Column("entry_id", sa.String(length=36), primary_key=True),
        sa.Column("booking_id", sa.String(length=36), sa.ForeignKey("bookings.booking_id"), nullable=False),
        sa.Column("worker_id", sa.String(length=255), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_seconds", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("segments", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("booking_id", name="uq_work_time_booking"),
    )


def downgrade() -> None:
    op.drop_table("work_time_entries")
    op.drop_column("bookings", "actual_seconds")
    op.drop_column("bookings", "planned_minutes")
