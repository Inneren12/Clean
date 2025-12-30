"""invoice payments support bookings and stripe refs

Revision ID: 0029_invoice_payment_links
Revises: 0028_team_scheduling_tables
Create Date: 2024-07-06 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0029_invoice_payment_links"
down_revision = "0028_team_scheduling_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("invoice_payments") as batch_op:
        batch_op.alter_column(
            "invoice_id",
            existing_type=sa.String(length=36),
            nullable=True,
        )
        batch_op.add_column(sa.Column("booking_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("checkout_session_id", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("payment_intent_id", sa.String(length=255), nullable=True))
        batch_op.create_index(
            "ix_invoice_payments_booking_id", ["booking_id"], unique=False
        )
        batch_op.create_index(
            "ix_invoice_payments_checkout_session", ["checkout_session_id"], unique=False
        )
        batch_op.create_foreign_key(
            "fk_invoice_payments_booking",
            "bookings",
            ["booking_id"],
            ["booking_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("invoice_payments") as batch_op:
        batch_op.drop_constraint("fk_invoice_payments_booking", type_="foreignkey")
        batch_op.drop_index("ix_invoice_payments_checkout_session")
        batch_op.drop_index("ix_invoice_payments_booking_id")
        batch_op.drop_column("payment_intent_id")
        batch_op.drop_column("checkout_session_id")
        batch_op.drop_column("booking_id")
        batch_op.alter_column(
            "invoice_id",
            existing_type=sa.String(length=36),
            nullable=False,
        )
