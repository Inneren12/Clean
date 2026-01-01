"""order photo tombstones

Revision ID: 0038_order_photo_tombstones
Revises: 0037_email_dedupe_dlq_unsubscribe
Create Date: 2025-01-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

UUID_TYPE = sa.Uuid(as_uuid=True)


revision = "0038_order_photo_tombstones"
down_revision = "0037_email_dedupe_dlq_unsubscribe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "order_photo_tombstones",
        sa.Column("tombstone_id", UUID_TYPE, primary_key=True, nullable=False),
        sa.Column("org_id", UUID_TYPE, sa.ForeignKey("organizations.org_id", ondelete="CASCADE"), nullable=False),
        sa.Column("order_id", sa.String(length=36), sa.ForeignKey("bookings.booking_id", ondelete="CASCADE"), nullable=False),
        sa.Column("photo_id", sa.String(length=36), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(length=255), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_order_photo_tombstones_pending",
        "order_photo_tombstones",
        ["processed_at", "created_at"],
    )
    op.create_index(
        "ix_order_photo_tombstones_org",
        "order_photo_tombstones",
        ["org_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_order_photo_tombstones_pending", table_name="order_photo_tombstones")
    op.drop_index("ix_order_photo_tombstones_org", table_name="order_photo_tombstones")
    op.drop_table("order_photo_tombstones")
