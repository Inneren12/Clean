"""email events table

Revision ID: 0003_email_events
Revises: 0002_slots_v1
Create Date: 2025-03-01 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_email_events"
down_revision = "0002_slots_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_events",
        sa.Column("event_id", sa.String(length=36), primary_key=True),
        sa.Column("booking_id", sa.String(length=36), sa.ForeignKey("bookings.booking_id"), nullable=False),
        sa.Column("email_type", sa.String(length=64), nullable=False),
        sa.Column("recipient", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("body", sa.String(length=2000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_email_events_booking_type",
        "email_events",
        ["booking_id", "email_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_email_events_booking_type", table_name="email_events")
    op.drop_table("email_events")
