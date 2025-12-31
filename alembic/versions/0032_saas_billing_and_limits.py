"""
Revision ID: 0032_saas_billing_and_limits
Revises: 0031_saas_multitenant_auth
Create Date: 2025-04-01
"""

import sqlalchemy as sa
from alembic import op

revision = "0032_saas_billing_and_limits"
down_revision = "0031_saas_multitenant_auth"
branch_labels = None
depends_on = None

UUID_TYPE = sa.Uuid(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "organization_billing",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("org_id", UUID_TYPE, sa.ForeignKey("organizations.org_id", ondelete="CASCADE")),
        sa.Column("stripe_customer_id", sa.String(length=255), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(length=255), nullable=True),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="inactive", nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("org_id", name="uq_org_billing_org"),
    )

    op.create_table(
        "organization_usage_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("org_id", UUID_TYPE, sa.ForeignKey("organizations.org_id", ondelete="CASCADE")),
        sa.Column("metric", sa.String(length=64), nullable=False),
        sa.Column("quantity", sa.Integer(), server_default="1", nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Index("ix_usage_org_metric_created", "org_id", "metric", "created_at"),
    )

    conn = op.get_bind()
    org_ids = [row[0] for row in conn.execute(sa.text("SELECT org_id FROM organizations"))]
    if org_ids:
        insert_stmt = sa.text(
            "INSERT INTO organization_billing (org_id, plan_id, status) VALUES (:org_id, 'free', 'inactive')"
        )
        for org_id in org_ids:
            conn.execute(insert_stmt, {"org_id": org_id})


def downgrade() -> None:
    op.drop_table("organization_usage_events")
    op.drop_table("organization_billing")
