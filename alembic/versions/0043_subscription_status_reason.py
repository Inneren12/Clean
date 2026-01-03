"""add subscription status reason

Revision ID: 0043_subscription_status_reason
Revises: 0042_photo_review_and_signing
Create Date: 2025-06-01 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0043_subscription_status_reason"
down_revision = "96339be46688"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column("status_reason", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("subscriptions", "status_reason")
