"""Break glass sessions table

Revision ID: 0047_break_glass_sessions
Revises: 0046_admin_idempotency
Create Date: 2026-03-01
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0047_break_glass_sessions"
down_revision = "0046_admin_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "break_glass_sessions",
        sa.Column("session_id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("org_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("actor", sa.String(length=150), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.org_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash", name="uq_break_glass_token_hash"),
    )
    op.create_index("ix_break_glass_sessions_org", "break_glass_sessions", ["org_id"], unique=False)
    op.create_index(
        "ix_break_glass_sessions_org_expires",
        "break_glass_sessions",
        ["org_id", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_break_glass_sessions_org_expires", table_name="break_glass_sessions")
    op.drop_index("ix_break_glass_sessions_org", table_name="break_glass_sessions")
    op.drop_table("break_glass_sessions")
