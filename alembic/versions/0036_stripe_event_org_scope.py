"""
Add org_id to Stripe events for scoping.

Revision ID: 0036_stripe_event_org_scope
Revises: 0035_core_tables_org_id
Create Date: 2025-06-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0036_stripe_event_org_scope"
down_revision = "0035_core_tables_org_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "stripe_events",
        sa.Column(
            "org_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.org_id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index("ix_stripe_events_org_id", "stripe_events", ["org_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_stripe_events_org_id", table_name="stripe_events")
    op.drop_column("stripe_events", "org_id")
