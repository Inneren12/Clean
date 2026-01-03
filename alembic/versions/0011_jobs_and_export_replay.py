"""
Add job status tracking and export replay metadata

Revision ID: 0011_jobs_and_export_replay
Revises: 0010_invoices
Create Date: 2025-05-01 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_jobs_and_export_replay"
down_revision = "0010_invoices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_heartbeats",
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "job_heartbeats",
        sa.Column("last_error", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "job_heartbeats",
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "job_heartbeats",
        sa.Column(
            "consecutive_failures", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
    )
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        op.alter_column("job_heartbeats", "consecutive_failures", server_default=None)

    op.add_column(
        "export_events",
        sa.Column("target_url", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "export_events",
        sa.Column("payload", sa.JSON(), nullable=True),
    )
    op.add_column(
        "export_events",
        sa.Column(
            "replay_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
    )
    op.add_column(
        "export_events",
        sa.Column("last_replayed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "export_events",
        sa.Column("last_replayed_by", sa.String(length=128), nullable=True),
    )
    if bind.dialect.name != "sqlite":
        op.alter_column("export_events", "replay_count", server_default=None)


def downgrade() -> None:
    op.drop_column("export_events", "last_replayed_by")
    op.drop_column("export_events", "last_replayed_at")
    op.drop_column("export_events", "replay_count")
    op.drop_column("export_events", "payload")
    op.drop_column("export_events", "target_url")

    op.drop_column("job_heartbeats", "consecutive_failures")
    op.drop_column("job_heartbeats", "last_error_at")
    op.drop_column("job_heartbeats", "last_error")
    op.drop_column("job_heartbeats", "last_success_at")
