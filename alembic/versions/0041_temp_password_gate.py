"""temp password enforcement and audit

Revision ID: 0041_temp_password_gate
Revises: 0040_order_photo_storage_provider
Create Date: 2025-05-14 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

UUID_TYPE = sa.Uuid(as_uuid=True)


# revision identifiers, used by Alembic.
revision = "0041_temp_password_gate"
down_revision = "0040_order_photo_storage_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "users",
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("temp_password_issued_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "password_reset_events",
        sa.Column("event_id", UUID_TYPE, primary_key=True, nullable=False),
        sa.Column("org_id", UUID_TYPE, sa.ForeignKey("organizations.org_id", ondelete="CASCADE")),
        sa.Column("user_id", UUID_TYPE, sa.ForeignKey("users.user_id", ondelete="CASCADE")),
        sa.Column("actor_admin", sa.String(length=255), nullable=True),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

def downgrade() -> None:
    op.drop_table("password_reset_events")
    op.drop_column("users", "temp_password_issued_at")
    op.drop_column("users", "password_changed_at")
    op.drop_column("users", "must_change_password")
