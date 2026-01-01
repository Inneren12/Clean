"""
Add email dedupe keys, DLQ table, and unsubscribe preferences.

Revision ID: 0037_email_dedupe_dlq_unsubscribe
Revises: 0036_stripe_event_org_scope
Create Date: 2025-07-01
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = "0037_email_dedupe_dlq_unsubscribe"
down_revision = "0036_stripe_event_org_scope"
branch_labels = None
depends_on = None

UUID_TYPE = sa.Uuid(as_uuid=True)
DEFAULT_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name if bind else ""

    # Ensure the Alembic version table can store this revision id (33+ chars).
    if dialect == "sqlite":
        try:
            with op.batch_alter_table("alembic_version") as batch_op:
                batch_op.alter_column(
                    "version_num",
                    existing_type=sa.String(length=32),
                    type_=sa.String(length=64),
                    existing_nullable=False,
                )
        except sa.exc.OperationalError:
            # SQLite may not support widening in-place; lengths are not enforced there.
            pass
    else:
        op.alter_column(
            "alembic_version",
            "version_num",
            existing_type=sa.String(length=32),
            type_=sa.String(length=64),
            existing_nullable=False,
        )
    op.add_column("email_events", sa.Column("dedupe_key", sa.String(length=255), nullable=True))

    date_expr = "strftime('%Y-%m-%d', created_at)" if dialect == "sqlite" else "to_char(date(created_at), 'YYYY-MM-DD')"
    op.execute(
        f"""
        UPDATE email_events
        SET dedupe_key = CASE
            WHEN invoice_id IS NOT NULL THEN 'invoice:' || invoice_id || ':' || email_type || ':' || lower(recipient)
            WHEN booking_id IS NOT NULL THEN 'booking:' || booking_id || ':' || email_type || ':' || lower(recipient)
            ELSE 'generic:' || email_type || ':' || lower(recipient) || ':' || COALESCE({date_expr}, 'unknown')
        END
        """
    )

    if dialect == "sqlite":
        with op.batch_alter_table("email_events") as batch_op:
            batch_op.alter_column("dedupe_key", nullable=False)
            batch_op.create_unique_constraint(
                "uq_email_events_org_dedupe", ["org_id", "dedupe_key"]
            )
    else:
        op.alter_column("email_events", "dedupe_key", nullable=False)
        op.create_unique_constraint(
            "uq_email_events_org_dedupe", "email_events", ["org_id", "dedupe_key"]
        )

    op.create_table(
        "email_failures",
        sa.Column("failure_id", sa.String(length=36), primary_key=True),
        sa.Column("org_id", UUID_TYPE, nullable=False, server_default=sa.text(f"'{DEFAULT_ORG_ID}'")),
        sa.Column("email_event_id", sa.String(length=36), sa.ForeignKey("email_events.event_id"), nullable=True),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("email_type", sa.String(length=64), nullable=False),
        sa.Column("recipient", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("body", sa.String(length=2000), nullable=False),
        sa.Column("booking_id", sa.String(length=36), sa.ForeignKey("bookings.booking_id"), nullable=True),
        sa.Column("invoice_id", sa.String(length=36), sa.ForeignKey("invoices.invoice_id"), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("last_error", sa.String(length=255), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_email_failures_org_status",
        "email_failures",
        ["org_id", "status", "next_retry_at"],
        unique=False,
    )
    op.create_index(
        "ix_email_failures_org_dedupe", "email_failures", ["org_id", "dedupe_key"], unique=False
    )

    op.create_table(
        "unsubscribe",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("org_id", UUID_TYPE, nullable=False, server_default=sa.text(f"'{DEFAULT_ORG_ID}'")),
        sa.Column("recipient", sa.String(length=255), nullable=False),
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("org_id", "recipient", "scope", name="uq_unsubscribe_recipient_scope"),
    )
    op.create_index(
        "ix_unsubscribe_org_recipient", "unsubscribe", ["org_id", "recipient"], unique=False
    )


def downgrade() -> None:
    # Leave alembic_version.version_num widened; shrinking risks truncation.
    op.drop_index("ix_unsubscribe_org_recipient", table_name="unsubscribe")
    op.drop_table("unsubscribe")

    op.drop_index("ix_email_failures_org_dedupe", table_name="email_failures")
    op.drop_index("ix_email_failures_org_status", table_name="email_failures")
    op.drop_table("email_failures")

    op.drop_constraint("uq_email_events_org_dedupe", "email_events", type_="unique")
    op.drop_column("email_events", "dedupe_key")
