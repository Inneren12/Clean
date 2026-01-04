"""Add metadata columns to stripe_events

Revision ID: 0049_stripe_event_metadata
Revises: 0048_admin_totp_mfa
Create Date: 2025-06-20
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0049_stripe_event_metadata"
down_revision = "0048_admin_totp_mfa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("stripe_events", recreate="auto") as batch:
        batch.add_column(sa.Column("event_type", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("event_created_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("invoice_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("booking_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("last_error", sa.Text(), nullable=True))
        batch.create_index("ix_stripe_events_invoice_id", ["invoice_id"], unique=False)
        batch.create_index("ix_stripe_events_booking_id", ["booking_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("stripe_events", recreate="auto") as batch:
        batch.drop_index("ix_stripe_events_booking_id")
        batch.drop_index("ix_stripe_events_invoice_id")
        batch.drop_column("last_error")
        batch.drop_column("booking_id")
        batch.drop_column("invoice_id")
        batch.drop_column("event_created_at")
        batch.drop_column("event_type")
