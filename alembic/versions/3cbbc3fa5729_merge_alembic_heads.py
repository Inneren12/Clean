"""merge alembic heads

Revision ID: 3cbbc3fa5729
Revises: 0042_photo_review_and_signing
Create Date: 2026-01-03 07:44:23.941327

"""
from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401

revision = '3cbbc3fa5729'
down_revision = ('0042_photo_review_and_signing',)
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
