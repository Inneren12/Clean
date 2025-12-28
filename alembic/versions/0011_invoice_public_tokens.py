"""Add invoice public tokens

Revision ID: 0011_invoice_public_tokens
Revises: 0010_invoices
Create Date: 2025-04-20 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_invoice_public_tokens"
down_revision = "0010_invoices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoice_public_tokens",
        sa.Column("token_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("invoice_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.invoice_id"], name="fk_invoice_public_tokens_invoice"),
        sa.UniqueConstraint("invoice_id", name="uq_invoice_public_tokens_invoice"),
        sa.UniqueConstraint("token_hash", name="uq_invoice_public_tokens_hash"),
    )
    op.create_index("ix_invoice_public_tokens_invoice_id", "invoice_public_tokens", ["invoice_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_invoice_public_tokens_invoice_id", table_name="invoice_public_tokens")
    op.drop_table("invoice_public_tokens")
