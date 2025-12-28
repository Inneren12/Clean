"""checklist tables

Revision ID: 0014_checklists
Revises: 0013_time_tracking
Create Date: 2024-07-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0014_checklists"
down_revision = "0013_time_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "checklist_templates",
        sa.Column("template_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("service_type", sa.String(length=64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
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
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("service_type", "version", name="uq_checklist_template_version"),
    )

    op.create_table(
        "checklist_template_items",
        sa.Column("item_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "template_id",
            sa.Integer(),
            sa.ForeignKey("checklist_templates.template_id"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("phase", sa.String(length=16), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )

    op.create_table(
        "checklist_runs",
        sa.Column("run_id", sa.String(length=36), primary_key=True),
        sa.Column("order_id", sa.String(length=36), sa.ForeignKey("bookings.booking_id"), nullable=False),
        sa.Column("template_id", sa.Integer(), sa.ForeignKey("checklist_templates.template_id"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="in_progress"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("order_id", name="uq_checklist_run_order"),
    )

    op.create_table(
        "checklist_run_items",
        sa.Column("run_item_id", sa.String(length=36), primary_key=True),
        sa.Column("run_id", sa.String(length=36), sa.ForeignKey("checklist_runs.run_id"), nullable=False),
        sa.Column(
            "template_item_id", sa.Integer(), sa.ForeignKey("checklist_template_items.item_id"), nullable=False
        ),
        sa.Column("checked", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("checklist_run_items")
    op.drop_table("checklist_runs")
    op.drop_table("checklist_template_items")
    op.drop_table("checklist_templates")
