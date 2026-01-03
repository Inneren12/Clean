"""merge alembic heads

Revision ID: 7f871a8d46f5
Revises: 3cbbc3fa5729
Create Date: 2026-01-03 07:58:03.738105

"""
from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401

revision = '7f871a8d46f5'
down_revision = '3cbbc3fa5729'
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
