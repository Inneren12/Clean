"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401

revision = '${up_revision}'
down_revision = ${down_revision | repr}
branch_labels = ${"None" if branch_labels in (None, "None") else repr(branch_labels)}
depends_on = ${"None" if depends_on in (None, "None") else repr(depends_on)}


def upgrade():
    ${upgrades if upgrades else 'pass'}


def downgrade():
    ${downgrades if downgrades else 'pass'}
