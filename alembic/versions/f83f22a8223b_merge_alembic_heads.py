"""merge alembic heads

Revision ID: f83f22a8223b
Revises: 7f871a8d46f5
Create Date: 2026-01-03 08:17:07.270029

"""
from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401

revision = 'f83f22a8223b'
down_revision = ('7f871a8d46f5',)
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
