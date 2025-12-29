"""add disputes schema and financial tracking columns

Revision ID: 0024_disputes_schema
Revises: 0023_policy_override_audit
Create Date: 2025-12-29 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0024_disputes_schema"
down_revision = "0023_policy_override_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add financial tracking columns to bookings
    op.add_column(
        "bookings",
        sa.Column(
            "base_charge_cents",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "bookings",
        sa.Column(
            "refund_total_cents",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "bookings",
        sa.Column(
            "credit_note_total_cents",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )

    # Create disputes table
    op.create_table(
        "disputes",
        sa.Column("dispute_id", sa.String(length=36), primary_key=True),
        sa.Column(
            "booking_id",
            sa.String(length=36),
            sa.ForeignKey("bookings.booking_id"),
            nullable=False,
        ),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("opened_by", sa.String(length=100), nullable=True),
        sa.Column("facts_snapshot", sa.JSON(), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=True),
        sa.Column("decision_cents", sa.Integer(), nullable=True),
        sa.Column("decision_notes", sa.String(length=500), nullable=True),
        sa.Column("decision_snapshot", sa.JSON(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # Create indexes for disputes
    op.create_index("ix_disputes_booking_id", "disputes", ["booking_id"])
    op.create_index("ix_disputes_booking_state", "disputes", ["booking_id", "state"])

    # Create financial_adjustment_events table
    op.create_table(
        "financial_adjustment_events",
        sa.Column("event_id", sa.String(length=36), primary_key=True),
        sa.Column(
            "booking_id",
            sa.String(length=36),
            sa.ForeignKey("bookings.booking_id"),
            nullable=False,
        ),
        sa.Column(
            "dispute_id",
            sa.String(length=36),
            sa.ForeignKey("disputes.dispute_id"),
            nullable=False,
        ),
        sa.Column("adjustment_type", sa.String(length=32), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("before_totals", sa.JSON(), nullable=False),
        sa.Column("after_totals", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # Create indexes for financial_adjustment_events
    op.create_index(
        "ix_financial_adjustment_events_booking_id",
        "financial_adjustment_events",
        ["booking_id"],
    )
    op.create_index(
        "ix_financial_adjustment_events_dispute_id",
        "financial_adjustment_events",
        ["dispute_id"],
    )
    op.create_index(
        "ix_financial_events_booking",
        "financial_adjustment_events",
        ["booking_id", "created_at"],
    )


def downgrade() -> None:
    # Drop indexes and tables in reverse order
    op.drop_index("ix_financial_events_booking", table_name="financial_adjustment_events")
    op.drop_index(
        "ix_financial_adjustment_events_dispute_id", table_name="financial_adjustment_events"
    )
    op.drop_index(
        "ix_financial_adjustment_events_booking_id", table_name="financial_adjustment_events"
    )
    op.drop_table("financial_adjustment_events")

    op.drop_index("ix_disputes_booking_state", table_name="disputes")
    op.drop_index("ix_disputes_booking_id", table_name="disputes")
    op.drop_table("disputes")

    # Drop booking columns
    op.drop_column("bookings", "credit_note_total_cents")
    op.drop_column("bookings", "refund_total_cents")
    op.drop_column("bookings", "base_charge_cents")
