"""event logs and actual duration

Revision ID: 0006_event_logs
Revises: 0005_deposits
Create Date: 2024-05-05 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0006_event_logs"
down_revision = "0005_deposits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bookings", sa.Column("actual_duration_minutes", sa.Integer(), nullable=True))

    op.create_table(
        "event_logs",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("lead_id", sa.String(length=36), nullable=True),
        sa.Column("booking_id", sa.String(length=36), nullable=True),
        sa.Column("estimated_revenue_cents", sa.Integer(), nullable=True),
        sa.Column("estimated_duration_minutes", sa.Integer(), nullable=True),
        sa.Column("actual_duration_minutes", sa.Integer(), nullable=True),
        sa.Column("utm_source", sa.String(length=100), nullable=True),
        sa.Column("utm_medium", sa.String(length=100), nullable=True),
        sa.Column("utm_campaign", sa.String(length=100), nullable=True),
        sa.Column("utm_term", sa.String(length=100), nullable=True),
        sa.Column("utm_content", sa.String(length=100), nullable=True),
        sa.Column("referrer", sa.String(length=255), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["booking_id"], ["bookings.booking_id"], ),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.lead_id"], ),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(op.f("ix_event_logs_type_time"), "event_logs", ["event_type", "occurred_at"], unique=False)
    op.create_index("ix_event_logs_booking_type", "event_logs", ["booking_id", "event_type"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_event_logs_booking_type", table_name="event_logs")
    op.drop_index(op.f("ix_event_logs_type_time"), table_name="event_logs")
    op.drop_table("event_logs")
    op.drop_column("bookings", "actual_duration_minutes")
