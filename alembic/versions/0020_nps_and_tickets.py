"""add nps responses and support tickets

Revision ID: 0020_nps_and_tickets
Revises: 0019_addons
Create Date: 2024-06-30
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0020_nps_and_tickets"
down_revision: Union[str, None] = "0019_addons"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "nps_responses",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("client_id", sa.String(length=36), nullable=True),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("comment", sa.String(length=2000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["order_id"], ["bookings.booking_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["client_users.client_id"]),
        sa.UniqueConstraint("order_id", name="uq_nps_responses_order"),
    )

    op.create_table(
        "support_tickets",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("client_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="OPEN"),
        sa.Column("priority", sa.String(length=32), nullable=False, server_default="normal"),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("body", sa.String(length=4000), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["order_id"], ["bookings.booking_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["client_users.client_id"]),
        sa.UniqueConstraint("order_id", name="uq_support_tickets_order"),
    )


def downgrade() -> None:
    op.drop_table("support_tickets")
    op.drop_table("nps_responses")
