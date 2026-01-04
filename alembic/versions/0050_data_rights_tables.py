"""data rights support tables

Revision ID: 0050_data_rights
Revises: 0049_stripe_event_metadata
Create Date: 2025-01-01 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0050_data_rights"
down_revision: Union[str, None] = "bc6a9a9f5c2b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "data_deletion_requests",
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("lead_id", sa.String(length=36), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("requested_by", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_notes", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.org_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("request_id"),
    )
    op.create_index(
        "ix_data_deletion_requests_org_status",
        "data_deletion_requests",
        ["org_id", "status"],
    )
    op.create_index(
        "ix_data_deletion_requests_org_created",
        "data_deletion_requests",
        ["org_id", "requested_at"],
    )

    op.add_column(
        "leads",
        sa.Column(
            "pending_deletion", sa.Boolean(), server_default="0", nullable=False
        ),
    )
    op.add_column(
        "leads", sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("leads", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "deleted_at")
    op.drop_column("leads", "deletion_requested_at")
    op.drop_column("leads", "pending_deletion")
    op.drop_index("ix_data_deletion_requests_org_created", table_name="data_deletion_requests")
    op.drop_index("ix_data_deletion_requests_org_status", table_name="data_deletion_requests")
    op.drop_table("data_deletion_requests")
