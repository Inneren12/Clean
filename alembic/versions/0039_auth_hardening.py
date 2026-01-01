"""
Auth hardening sessions and token audit
"""

from alembic import op
import sqlalchemy as sa


revision = "0039_auth_hardening"
down_revision = "0038_order_photo_tombstones"
branch_labels = None
depends_on = None

UUID_TYPE = sa.Uuid(as_uuid=True)
membership_role_enum = sa.Enum(
    "owner", "admin", "dispatcher", "finance", "viewer", "worker", name="membershiprole"
)


def upgrade() -> None:
    op.create_table(
        "saas_sessions",
        sa.Column("session_id", UUID_TYPE, primary_key=True),
        sa.Column("user_id", UUID_TYPE, sa.ForeignKey("users.user_id", ondelete="CASCADE")),
        sa.Column("org_id", UUID_TYPE, sa.ForeignKey("organizations.org_id", ondelete="CASCADE")),
        sa.Column("role", membership_role_enum, nullable=False),
        sa.Column("refresh_token_hash", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_from", UUID_TYPE, nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_saas_sessions_user", "saas_sessions", ["user_id"])
    op.create_index("ix_saas_sessions_org", "saas_sessions", ["org_id"])

    op.create_table(
        "token_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", UUID_TYPE, sa.ForeignKey("saas_sessions.session_id", ondelete="CASCADE")),
        sa.Column("user_id", UUID_TYPE, sa.ForeignKey("users.user_id", ondelete="CASCADE")),
        sa.Column("org_id", UUID_TYPE, sa.ForeignKey("organizations.org_id", ondelete="CASCADE")),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("token_type", sa.String(length=32), nullable=False),
        sa.Column("actor_role", membership_role_enum, nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("metadata", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_token_events_session", "token_events", ["session_id"])
    op.create_index("ix_token_events_user", "token_events", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_token_events_user", table_name="token_events")
    op.drop_index("ix_token_events_session", table_name="token_events")
    op.drop_table("token_events")
    op.drop_index("ix_saas_sessions_org", table_name="saas_sessions")
    op.drop_index("ix_saas_sessions_user", table_name="saas_sessions")
    op.drop_table("saas_sessions")
