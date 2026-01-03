"""merge migration heads

Revision ID: f8dba77650d4
Revises: 0011_jobs_and_export_replay, 0041_temp_password_gate
Create Date: 2025-05-15 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "f8dba77650d4"
down_revision = ("0011_jobs_and_export_replay", "0041_temp_password_gate")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
