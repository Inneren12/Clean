"""stripe event metadata

Revision ID: 0049_stripe_event_metadata
Revises: 0048_admin_totp_mfa
Create Date: 2026-01-04 03:46:01.949436

"""
from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401

revision = '0049_stripe_event_metadata'
down_revision = '0048_admin_totp_mfa'
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
