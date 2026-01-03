"""merge alembic heads

Revision ID: 96339be46688
Revises: f83f22a8223b, f8dba77650d4
Create Date: 2026-01-03 08:29:37.189129

"""
from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401

revision = '96339be46688'
down_revision = (
    "f83f22a8223b",
    "f8dba77650d4",
)
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
