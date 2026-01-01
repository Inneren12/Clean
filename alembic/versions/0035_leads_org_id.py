"""add org_id to leads table

Revision ID: 0035_leads_org_id
Revises: 0034_org_id_uuid_and_default_org
Create Date: 2026-01-01
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = "0035_leads_org_id"
down_revision = "0034_org_id_uuid_and_default_org"
branch_labels = None
depends_on = None

DEFAULT_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def upgrade() -> None:
    # Add org_id column with default value
    op.add_column(
        "leads",
        sa.Column(
            "org_id",
            sa.Uuid(as_uuid=True),
            nullable=False,
            server_default=str(DEFAULT_ORG_ID),
        ),
    )

    # Add foreign key constraint
    op.create_foreign_key(
        "fk_leads_org_id_organizations",
        "leads",
        "organizations",
        ["org_id"],
        ["org_id"],
        ondelete="CASCADE",
    )

    # Create index for better query performance
    op.create_index(
        "ix_leads_org_id",
        "leads",
        ["org_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_leads_org_id", table_name="leads")
    op.drop_constraint("fk_leads_org_id_organizations", "leads", type_="foreignkey")
    op.drop_column("leads", "org_id")
