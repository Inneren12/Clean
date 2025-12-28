"""reason logs table

Revision ID: 0016_reason_logs
Revises: 0015_order_photos
Create Date: 2025-02-18 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0016_reason_logs"
down_revision = "0015_order_photos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reason_logs",
        sa.Column("reason_id", sa.String(length=36), primary_key=True),
        sa.Column("order_id", sa.String(length=36), sa.ForeignKey("bookings.booking_id"), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("note", sa.String(length=1000)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(length=100)),
        sa.Column("time_entry_id", sa.String(length=36), sa.ForeignKey("work_time_entries.entry_id")),
        sa.Column("invoice_item_id", sa.Integer(), sa.ForeignKey("invoice_items.item_id")),
    )
    op.create_index("ix_reason_logs_order", "reason_logs", ["order_id"])
    op.create_index("ix_reason_logs_kind", "reason_logs", ["kind"])
    op.create_index("ix_reason_logs_created_at", "reason_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_reason_logs_created_at", table_name="reason_logs")
    op.drop_index("ix_reason_logs_kind", table_name="reason_logs")
    op.drop_index("ix_reason_logs_order", table_name="reason_logs")
    op.drop_table("reason_logs")
