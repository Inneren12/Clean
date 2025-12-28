"""stripe invoice payments

Revision ID: 0012_stripe_invoice_payments
Revises: 0011_invoice_public_tokens
Create Date: 2024-01-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0012_stripe_invoice_payments"
down_revision = "0011_invoice_public_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoice_payments", sa.Column("provider_ref", sa.String(length=255), nullable=True))
    op.create_index(
        "ix_invoice_payments_provider_ref", "invoice_payments", ["provider_ref"], unique=False
    )
    op.create_unique_constraint(
        "uq_invoice_payments_provider_ref",
        "invoice_payments",
        ["provider", "provider_ref"],
    )

    op.create_table(
        "stripe_events",
        sa.Column("event_id", sa.String(length=255), primary_key=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload_hash", sa.String(length=128), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_stripe_events_payload_hash", "stripe_events", ["payload_hash"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_stripe_events_payload_hash", table_name="stripe_events")
    op.drop_table("stripe_events")

    op.drop_constraint("uq_invoice_payments_provider_ref", "invoice_payments", type_="unique")
    op.drop_index("ix_invoice_payments_provider_ref", table_name="invoice_payments")
    op.drop_column("invoice_payments", "provider_ref")
