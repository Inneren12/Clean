"""Invoice tax snapshots

Revision ID: 0049_invoice_tax_snapshots
Revises: 0048_admin_totp_mfa
Create Date: 2026-05-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0049_invoice_tax_snapshots"
down_revision = "0048_admin_totp_mfa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column(
            "taxable_subtotal_cents",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "invoices",
        sa.Column("tax_rate_basis", sa.Numeric(6, 4), nullable=True),
    )

    invoices = sa.table(
        "invoices",
        sa.column("invoice_id", sa.String(length=36)),
        sa.column("subtotal_cents", sa.Integer()),
        sa.column("tax_cents", sa.Integer()),
        sa.column("taxable_subtotal_cents", sa.Integer()),
        sa.column("tax_rate_basis", sa.Numeric(6, 4)),
    )
    invoice_items = sa.table(
        "invoice_items",
        sa.column("invoice_id", sa.String(length=36)),
        sa.column("line_total_cents", sa.Integer()),
        sa.column("tax_rate", sa.Numeric(5, 4)),
    )

    connection = op.get_bind()
    taxable_subtotals = connection.execute(
        sa.select(
            invoice_items.c.invoice_id,
            sa.func.coalesce(
                sa.func.sum(
                    sa.case(
                        (
                            sa.and_(
                                invoice_items.c.tax_rate.isnot(None),
                                invoice_items.c.tax_rate != 0,
                            ),
                            invoice_items.c.line_total_cents,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("taxable_subtotal"),
        ).group_by(invoice_items.c.invoice_id)
    ).all()

    for invoice_id, taxable_subtotal in taxable_subtotals:
        connection.execute(
            invoices.update()
            .where(invoices.c.invoice_id == invoice_id)
            .values(taxable_subtotal_cents=int(taxable_subtotal or 0))
        )

    connection.execute(
        invoices.update()
        .where(invoices.c.taxable_subtotal_cents == 0)
        .values(taxable_subtotal_cents=invoices.c.subtotal_cents)
    )

    connection.execute(
        invoices.update()
        .where(invoices.c.taxable_subtotal_cents > 0)
        .values(
            tax_rate_basis=sa.func.round(
                invoices.c.tax_cents
                / sa.cast(invoices.c.taxable_subtotal_cents, sa.Numeric(12, 4)),
                4,
            )
        )
    )

    if connection.dialect.name != "sqlite":
        op.alter_column("invoices", "taxable_subtotal_cents", server_default=None)


def downgrade() -> None:
    op.drop_column("invoices", "tax_rate_basis")
    op.drop_column("invoices", "taxable_subtotal_cents")
