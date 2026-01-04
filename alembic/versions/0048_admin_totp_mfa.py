"""Admin TOTP MFA for SaaS users

Revision ID: 0048_admin_totp_mfa
Revises: 0047_break_glass_sessions
Create Date: 2026-04-01
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0048_admin_totp_mfa"
down_revision = "0047_break_glass_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("totp_secret_base32", sa.String(length=128), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "totp_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "users",
        sa.Column("totp_enrolled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "saas_sessions",
        sa.Column(
            "mfa_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("saas_sessions", "mfa_verified")
    op.drop_column("users", "totp_enrolled_at")
    op.drop_column("users", "totp_enabled")
    op.drop_column("users", "totp_secret_base32")
