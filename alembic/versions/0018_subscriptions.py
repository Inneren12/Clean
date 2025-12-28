"""
subscriptions

Revision ID: 0018_subscriptions
Revises: 0017_client_portal_linear
Create Date: 2025-05-15
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0018_subscriptions"
down_revision = "0017_client_portal_linear"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.create_table(
        "subscriptions",
        sa.Column("subscription_id", sa.String(length=36), primary_key=True),
        sa.Column("client_id", sa.String(length=36), sa.ForeignKey("client_users.client_id"), index=True, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("frequency", sa.String(length=16), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("preferred_weekday", sa.Integer(), nullable=True),
        sa.Column("preferred_day_of_month", sa.Integer(), nullable=True),
        sa.Column("base_service_type", sa.String(length=100), nullable=False),
        sa.Column("base_price", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "subscription_addons",
        sa.Column("subscription_addon_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("subscription_id", sa.String(length=36), sa.ForeignKey("subscriptions.subscription_id", ondelete="CASCADE"), index=True, nullable=False),
        sa.Column("addon_code", sa.String(length=100), nullable=False),
        sa.UniqueConstraint("subscription_id", "addon_code", name="uq_subscription_addons_code"),
    )

    if is_sqlite:
        with op.batch_alter_table("bookings") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "subscription_id",
                    sa.String(length=36),
                    sa.ForeignKey(
                        "subscriptions.subscription_id",
                        name="fk_bookings_subscription_id_subscriptions",
                    ),
                    nullable=True,
                )
            )
            batch_op.add_column(sa.Column("scheduled_date", sa.Date(), nullable=True))
            batch_op.create_index("ix_bookings_subscription_id", ["subscription_id"])
            batch_op.create_unique_constraint(
                "uq_bookings_subscription_schedule", ["subscription_id", "scheduled_date"]
            )
    else:
        op.add_column(
            "bookings",
            sa.Column(
                "subscription_id",
                sa.String(length=36),
                sa.ForeignKey(
                    "subscriptions.subscription_id",
                    name="fk_bookings_subscription_id_subscriptions",
                ),
                nullable=True,
            ),
        )
        op.add_column("bookings", sa.Column("scheduled_date", sa.Date(), nullable=True))
        op.create_index("ix_bookings_subscription_id", "bookings", ["subscription_id"])
        op.create_unique_constraint(
            "uq_bookings_subscription_schedule", "bookings", ["subscription_id", "scheduled_date"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    if is_sqlite:
        with op.batch_alter_table("bookings") as batch_op:
            batch_op.drop_constraint("uq_bookings_subscription_schedule", type_="unique")
            batch_op.drop_index("ix_bookings_subscription_id")
            batch_op.drop_column("scheduled_date")
            batch_op.drop_column("subscription_id")
    else:
        op.drop_constraint("uq_bookings_subscription_schedule", "bookings", type_="unique")
        op.drop_index("ix_bookings_subscription_id", table_name="bookings")
        op.drop_column("bookings", "scheduled_date")
        op.drop_column("bookings", "subscription_id")
    op.drop_table("subscription_addons")
    op.drop_table("subscriptions")
