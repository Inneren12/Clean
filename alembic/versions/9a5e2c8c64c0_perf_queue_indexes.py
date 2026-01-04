"""Add pagination-friendly indexes for queues and exports

Revision ID: 9a5e2c8c64c0
Revises: f8dba77650d4
Create Date: 2025-07-01 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "9a5e2c8c64c0"
down_revision = "a2cce6391ad9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_order_photos_org_review_status_created",
        "order_photos",
        ["org_id", "review_status", "created_at"],
    )
    op.create_index(
        "ix_order_photos_org_retake_created",
        "order_photos",
        ["org_id", "needs_retake", "created_at"],
    )
    op.create_index(
        "ix_bookings_org_unassigned_start",
        "bookings",
        ["org_id", "assigned_worker_id", "status", "starts_at"],
    )
    op.create_index(
        "ix_export_events_org_error_created",
        "export_events",
        ["org_id", "last_error_code", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_export_events_org_error_created", table_name="export_events")
    op.drop_index("ix_bookings_org_unassigned_start", table_name="bookings")
    op.drop_index("ix_order_photos_org_retake_created", table_name="order_photos")
    op.drop_index("ix_order_photos_org_review_status_created", table_name="order_photos")
