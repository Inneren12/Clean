"""add audit trail for policy overrides

Revision ID: 0023_policy_override_audit
Revises: 0022_booking_risk_fields
Create Date: 2025-02-20 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0023_policy_override_audit"
down_revision = "0022_booking_risk_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bookings",
        sa.Column(
            "cancellation_exception",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "bookings",
        sa.Column("cancellation_exception_note", sa.String(length=255), nullable=True),
    )

    op.create_table(
        "policy_override_audits",
        sa.Column("audit_id", sa.String(length=36), primary_key=True),
        sa.Column(
            "booking_id",
            sa.String(length=36),
            sa.ForeignKey("bookings.booking_id"),
            nullable=False,
        ),
        sa.Column("override_type", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("old_value", sa.JSON(), nullable=False),
        sa.Column("new_value", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_policy_override_booking_type",
        "policy_override_audits",
        ["booking_id", "override_type"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_policy_override_booking_type", table_name="policy_override_audits"
    )
    op.drop_table("policy_override_audits")
    op.drop_column("bookings", "cancellation_exception_note")
    op.drop_column("bookings", "cancellation_exception")
