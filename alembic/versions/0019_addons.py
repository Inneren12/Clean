"""add addons tables

Revision ID: 0019_addons
Revises: 0018_subscriptions
Create Date: 2024-05-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0019_addons"
down_revision: Union[str, None] = "0018_subscriptions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "addon_definitions",
        sa.Column("addon_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(length=100), nullable=False, unique=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("default_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
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
    )
    op.create_table(
        "order_addons",
        sa.Column("order_addon_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("addon_id", sa.Integer(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("unit_price_cents_snapshot", sa.Integer(), nullable=False),
        sa.Column("minutes_snapshot", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["addon_id"], ["addon_definitions.addon_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_id"], ["bookings.booking_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("order_id", "addon_id", name="uq_order_addons_order_addon"),
    )
    op.create_index("ix_order_addons_order_id", "order_addons", ["order_id"], unique=False)
    op.create_index("ix_order_addons_addon_id", "order_addons", ["addon_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_order_addons_addon_id", table_name="order_addons")
    op.drop_index("ix_order_addons_order_id", table_name="order_addons")
    op.drop_table("order_addons")
    op.drop_table("addon_definitions")
