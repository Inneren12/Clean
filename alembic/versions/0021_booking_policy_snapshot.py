"""
Add policy snapshot to bookings

Revision ID: 0021_booking_policy_snapshot
Revises: 0020_nps_and_tickets
Create Date: 2024-09-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0021_booking_policy_snapshot"
down_revision: Union[str, None] = "0020_nps_and_tickets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("bookings", sa.Column("policy_snapshot", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("bookings", "policy_snapshot")
