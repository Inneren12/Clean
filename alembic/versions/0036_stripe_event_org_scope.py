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
    with op.batch_alter_table("stripe_events", recreate="auto") as batch:
        batch.add_column(sa.Column("org_id", sa.Uuid(as_uuid=True), nullable=True))
        batch.create_foreign_key(
            "fk_stripe_events_org_id_organizations",
            "organizations",
            ["org_id"],
            ["org_id"],
            ondelete="CASCADE",
        )
        batch.create_index("ix_stripe_events_org_id", ["org_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("stripe_events", recreate="auto") as batch:
        batch.drop_constraint(
            "fk_stripe_events_org_id_organizations", type_="foreignkey"
        )
        batch.drop_index("ix_stripe_events_org_id")
        batch.drop_column("org_id")
