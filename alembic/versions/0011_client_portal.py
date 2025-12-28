"""
Add client users and attach to bookings

Revision ID: 0011_client_portal
Revises: 0010_invoices
Create Date: 2025-05-01 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import func


revision = "0011_client_portal"
down_revision = "0010_invoices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "client_users",
        sa.Column("client_id", sa.String(length=36), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False, unique=True),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=func.now(), nullable=False),
    )
    op.add_column("bookings", sa.Column("client_id", sa.String(length=36), nullable=True))
    op.create_foreign_key(
        "fk_bookings_client_users", "bookings", "client_users", ["client_id"], ["client_id"]
    )
    op.create_index("ix_bookings_client_id", "bookings", ["client_id"])


def downgrade() -> None:
    op.drop_index("ix_bookings_client_id", table_name="bookings")
    op.drop_constraint("fk_bookings_client_users", "bookings", type_="foreignkey")
    op.drop_column("bookings", "client_id")
    op.drop_table("client_users")
