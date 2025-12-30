"""Add invoice scope to email events

Revision ID: 0030_email_events_invoice_scope
Revises: 0029_invoice_payment_links
Create Date: 2025-03-11 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0030_email_events_invoice_scope"
down_revision = "0029_invoice_payment_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("email_events") as batch_op:
        batch_op.alter_column(
            "booking_id", existing_type=sa.String(length=36), nullable=True
        )
        batch_op.add_column(sa.Column("invoice_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_email_events_invoice_id",
            "invoices",
            ["invoice_id"],
            ["invoice_id"],
        )
        batch_op.create_index("ix_email_events_invoice_id", ["invoice_id"])
        batch_op.create_index(
            "ix_email_events_invoice_type", ["invoice_id", "email_type"]
        )


def downgrade() -> None:
    with op.batch_alter_table("email_events") as batch_op:
        batch_op.drop_index("ix_email_events_invoice_type")
        batch_op.drop_index("ix_email_events_invoice_id")
        batch_op.drop_constraint("fk_email_events_invoice_id", type_="foreignkey")
        batch_op.drop_column("invoice_id")
        batch_op.alter_column(
            "booking_id", existing_type=sa.String(length=36), nullable=False
        )
