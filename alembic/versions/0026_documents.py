"""
Add document templates and issued documents

Revision ID: 0026_documents
Revises: 0025_admin_audit_logs
Create Date: 2025-01-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0026_documents"
down_revision: Union[str, None] = "0025_admin_audit_logs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_templates",
        sa.Column("template_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("document_type", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("document_type", "version", name="uq_document_template_version"),
    )

    op.create_table(
        "documents",
        sa.Column("document_id", sa.String(length=36), primary_key=True),
        sa.Column("document_type", sa.String(length=64), nullable=False),
        sa.Column("reference_id", sa.String(length=64), nullable=False),
        sa.Column("template_id", sa.Integer(), sa.ForeignKey("document_templates.template_id"), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("pdf_bytes", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("document_type", "reference_id", name="uq_document_reference"),
    )
    op.create_index(
        "ix_documents_reference_type", "documents", ["reference_id", "document_type"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_documents_reference_type", table_name="documents")
    op.drop_table("documents")
    op.drop_table("document_templates")
