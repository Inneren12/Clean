"""
Add invoices, invoice items, and payments tables

Revision ID: 0010_invoices
Revises: 0009_export_events_dead_letter
Create Date: 2025-04-10 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_invoices"
down_revision = "0009_export_events_dead_letter"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoice_number_sequences",
        sa.Column("sequence_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("last_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("year", name="uq_invoice_number_sequences_year"),
    )

    op.create_table(
        "invoices",
        sa.Column("invoice_id", sa.String(length=36), primary_key=True),
        sa.Column("invoice_number", sa.String(length=32), nullable=False, unique=True),
        sa.Column("order_id", sa.String(length=36), nullable=True),
        sa.Column("customer_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("subtotal_cents", sa.Integer(), nullable=False),
        sa.Column("tax_cents", sa.Integer(), nullable=False),
        sa.Column("total_cents", sa.Integer(), nullable=False),
        sa.Column("notes", sa.String(length=1000), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["order_id"], ["bookings.booking_id"], name="fk_invoices_order"),
        sa.ForeignKeyConstraint(["customer_id"], ["leads.lead_id"], name="fk_invoices_customer"),
    )
    op.create_index("ix_invoices_order_id", "invoices", ["order_id"], unique=False)
    op.create_index("ix_invoices_customer_id", "invoices", ["customer_id"], unique=False)

    op.create_table(
        "invoice_items",
        sa.Column("item_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("invoice_id", sa.String(length=36), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("unit_price_cents", sa.Integer(), nullable=False),
        sa.Column("line_total_cents", sa.Integer(), nullable=False),
        sa.Column("tax_rate", sa.Numeric(5, 4), nullable=True),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.invoice_id"], name="fk_invoice_items_invoice"),
    )
    op.create_index("ix_invoice_items_invoice_id", "invoice_items", ["invoice_id"], unique=False)

    op.create_table(
        "invoice_payments",
        sa.Column("payment_id", sa.String(length=36), primary_key=True),
        sa.Column("invoice_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reference", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.invoice_id"], name="fk_invoice_payments_invoice"),
    )
    op.create_index(
        "ix_invoice_payments_invoice_status",
        "invoice_payments",
        ["invoice_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_invoice_payments_invoice_status", table_name="invoice_payments")
    op.drop_table("invoice_payments")
    op.drop_index("ix_invoice_items_invoice_id", table_name="invoice_items")
    op.drop_table("invoice_items")
    op.drop_index("ix_invoices_customer_id", table_name="invoices")
    op.drop_index("ix_invoices_order_id", table_name="invoices")
    op.drop_table("invoices")
    op.drop_table("invoice_number_sequences")
