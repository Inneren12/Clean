"""
Add client users and attach to bookings

Revision ID: 0017_client_portal_linear
Revises: 0016_reason_logs
Create Date: 2025-05-01 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import func


revision = "0017_client_portal_linear"
down_revision = "0016_reason_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.create_table(
        "client_users",
        sa.Column("client_id", sa.String(length=36), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False, unique=True),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=func.now(), nullable=False),
    )

    if is_sqlite:
        with op.batch_alter_table("bookings") as batch_op:
            batch_op.add_column(sa.Column("client_id", sa.String(length=36), nullable=True))
            batch_op.create_foreign_key(
                "fk_bookings_client_users", "client_users", ["client_id"], ["client_id"]
            )
            batch_op.create_index("ix_bookings_client_id", ["client_id"])
    else:
        op.add_column("bookings", sa.Column("client_id", sa.String(length=36), nullable=True))
        op.create_foreign_key(
            "fk_bookings_client_users", "bookings", "client_users", ["client_id"], ["client_id"]
        )
        op.create_index("ix_bookings_client_id", "bookings", ["client_id"])


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    if is_sqlite:
        with op.batch_alter_table("bookings") as batch_op:
            batch_op.drop_index("ix_bookings_client_id")
            batch_op.drop_constraint("fk_bookings_client_users", type_="foreignkey")
            batch_op.drop_column("client_id")
    else:
        op.drop_index("ix_bookings_client_id", table_name="bookings")
        op.drop_constraint("fk_bookings_client_users", "bookings", type_="foreignkey")
        op.drop_column("bookings", "client_id")
    op.drop_table("client_users")
