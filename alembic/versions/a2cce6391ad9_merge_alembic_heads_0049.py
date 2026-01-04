"""merge alembic heads (0049)

Revision ID: a2cce6391ad9
Revises: 0049_invoice_tax_snapshots, 0049_stripe_event_metadata
Create Date: 2026-01-04 03:46:05.392658

"""
from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401

revision = 'a2cce6391ad9'
down_revision = ("0049_invoice_tax_snapshots", "0049_stripe_event_metadata")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
