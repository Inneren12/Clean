"""
SaaS multitenant auth foundation.

Revision ID: 0031_saas_multitenant_auth
Revises: 0030_email_events_invoice_scope
Create Date: 2025-03-15
"""

from alembic import op
import sqlalchemy as sa
import uuid


UUID_TYPE = sa.Uuid(as_uuid=True)


revision = "0031_saas_multitenant_auth"
down_revision = "0030_email_events_invoice_scope"
branch_labels = None
depends_on = None


membership_role_enum = sa.Enum(
    "owner", "admin", "dispatcher", "finance", "viewer", "worker", name="membershiprole"
)


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("org_id", UUID_TYPE, primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "users",
        sa.Column("user_id", UUID_TYPE, primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=False)

    op.create_table(
        "memberships",
        sa.Column("membership_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("org_id", UUID_TYPE, sa.ForeignKey("organizations.org_id", ondelete="CASCADE")),
        sa.Column("user_id", UUID_TYPE, sa.ForeignKey("users.user_id", ondelete="CASCADE")),
        sa.Column("role", membership_role_enum, nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("org_id", "user_id", name="uq_memberships_org_user"),
    )

    op.create_table(
        "api_tokens",
        sa.Column("token_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("org_id", UUID_TYPE, sa.ForeignKey("organizations.org_id", ondelete="CASCADE")),
        sa.Column("token_hash", sa.String(length=255), nullable=False),
        sa.Column("role", membership_role_enum, nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_api_tokens_hash"),
    )

    default_org_id = uuid.uuid4()
    op.execute(
        sa.text("INSERT INTO organizations (org_id, name) VALUES (:org_id, :name)").bindparams(
            org_id=default_org_id, name="Default Org"
        )
    )


def downgrade() -> None:
    op.drop_table("api_tokens")
    op.drop_table("memberships")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    op.drop_table("organizations")
    membership_role_enum.drop(op.get_bind(), checkfirst=True)
