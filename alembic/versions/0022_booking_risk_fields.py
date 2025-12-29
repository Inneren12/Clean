"""add risk scoring fields to bookings

Revision ID: 0022_booking_risk_fields
Revises: 0021_booking_policy_snapshot
Create Date: 2024-05-05 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0022_booking_risk_fields"
down_revision = "0021_booking_policy_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bookings",
        sa.Column(
            "risk_score",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "bookings",
        sa.Column(
            "risk_band",
            sa.String(length=16),
            nullable=False,
            server_default="LOW",
        ),
    )
    op.add_column(
        "bookings",
        sa.Column(
            "risk_reasons",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("bookings", "risk_reasons")
    op.drop_column("bookings", "risk_band")
    op.drop_column("bookings", "risk_score")
