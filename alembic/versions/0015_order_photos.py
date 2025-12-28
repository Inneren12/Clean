"""order photos and consent flag

Revision ID: 0015_order_photos
Revises: 0014_checklists
Create Date: 2024-08-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0015_order_photos"
down_revision = "0014_checklists"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.add_column(
        "bookings",
        sa.Column("consent_photos", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "order_photos",
        sa.Column("photo_id", sa.String(length=36), primary_key=True),
        sa.Column("order_id", sa.String(length=36), sa.ForeignKey("bookings.booking_id", ondelete="CASCADE"), nullable=False),
        sa.Column("phase", sa.String(length=16), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("uploaded_by", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_order_photos_order_id", "order_photos", ["order_id"])

    op.execute("UPDATE bookings SET consent_photos = false WHERE consent_photos IS NULL")
    if not is_sqlite:
        op.alter_column("bookings", "consent_photos", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_order_photos_order_id", table_name="order_photos")
    op.drop_table("order_photos")
    op.drop_column("bookings", "consent_photos")
