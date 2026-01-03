"""Photo review workflow and signing fields

Revision ID: 0042_photo_review_and_signing
Revises: 0041_temp_password_gate
Create Date: 2025-01-01 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0042_photo_review_and_signing"
down_revision = "0041_temp_password_gate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.add_column(
        "order_photos",
        sa.Column("review_status", sa.String(length=16), nullable=False, server_default="PENDING"),
    )
    op.add_column(
        "order_photos",
        sa.Column("review_comment", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "order_photos",
        sa.Column("reviewed_by", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "order_photos",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "order_photos",
        sa.Column("needs_retake", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.execute(
        """
        UPDATE order_photos
        SET review_status = 'PENDING'
        WHERE review_status IS NULL
        """
    )

    if not is_sqlite:
        op.alter_column("order_photos", "review_status", server_default=None)
        op.alter_column("order_photos", "needs_retake", server_default=None)


def downgrade() -> None:
    op.drop_column("order_photos", "needs_retake")
    op.drop_column("order_photos", "reviewed_at")
    op.drop_column("order_photos", "reviewed_by")
    op.drop_column("order_photos", "review_comment")
    op.drop_column("order_photos", "review_status")
