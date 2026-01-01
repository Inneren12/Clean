"""
Add org_id to core business tables for multi-tenant isolation.

Revision ID: 0035_org_id_core_tables
Revises: 0034_org_id_uuid_and_default_org
Create Date: 2025-05-20
"""

from __future__ import annotations
"""
Add org_id to core business tables for multi-tenant isolation.

Revision ID: 0035_org_id_core_tables
Revises: 0034_org_id_uuid_and_default_org
Create Date: 2025-05-20
"""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "0035_org_id_core_tables"
down_revision = "0034_org_id_uuid_and_default_org"
branch_labels = None
depends_on = None

UUID_TYPE = sa.Uuid(as_uuid=True)
DEFAULT_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
DEFAULT_ORG_NAME = "Default Org"

TABLE_INDEXES: dict[str, list[tuple[str, list[str]]]] = {
    "teams": [
        ("ix_teams_org_id", ["org_id"]),
        ("ix_teams_org_created", ["org_id", "created_at"]),
    ],
    "team_working_hours": [
        ("ix_team_working_hours_org_team", ["org_id", "team_id"]),
        ("ix_team_working_hours_org_day", ["org_id", "day_of_week"]),
    ],
    "team_blackouts": [
        ("ix_team_blackouts_org_team", ["org_id", "team_id"]),
        ("ix_team_blackouts_org_starts", ["org_id", "starts_at"]),
    ],
    "bookings": [
        ("ix_bookings_org_id", ["org_id"]),
        ("ix_bookings_org_status", ["org_id", "status"]),
        ("ix_bookings_org_starts", ["org_id", "starts_at"]),
    ],
    "email_events": [
        ("ix_email_events_org_type", ["org_id", "email_type"]),
    ],
    "order_photos": [
        ("ix_order_photos_org_order", ["org_id", "order_id"]),
    ],
    "chat_sessions": [
        ("ix_chat_sessions_org_updated", ["org_id", "updated_at"]),
    ],
    "leads": [
        ("ix_leads_org_id", ["org_id"]),
        ("ix_leads_org_status", ["org_id", "status"]),
        ("ix_leads_org_created", ["org_id", "created_at"]),
    ],
    "referral_credits": [
        ("ix_referral_credits_org_referrer", ["org_id", "referrer_lead_id"]),
    ],
    "invoices": [
        ("ix_invoices_org_id", ["org_id"]),
        ("ix_invoices_org_status", ["org_id", "status"]),
        ("ix_invoices_org_created", ["org_id", "created_at"]),
    ],
    "invoice_items": [
        ("ix_invoice_items_org_invoice", ["org_id", "invoice_id"]),
    ],
    "invoice_payments": [
        ("ix_invoice_payments_org_id", ["org_id"]),
        ("ix_invoice_payments_org_status", ["org_id", "status"]),
    ],
    "invoice_public_tokens": [
        ("ix_invoice_public_tokens_org_invoice", ["org_id", "invoice_id"]),
    ],
    "workers": [
        ("ix_workers_org_id", ["org_id"]),
        ("ix_workers_org_active", ["org_id", "is_active"]),
    ],
    "subscriptions": [
        ("ix_subscriptions_org_status", ["org_id", "status"]),
        ("ix_subscriptions_org_next_run", ["org_id", "next_run_at"]),
    ],
    "subscription_addons": [
        ("ix_subscription_addons_org_subscription", ["org_id", "subscription_id"]),
    ],
    "disputes": [
        ("ix_disputes_org_state", ["org_id", "state"]),
    ],
    "financial_adjustment_events": [
        ("ix_financial_events_org_created", ["org_id", "created_at"]),
    ],
    "admin_audit_logs": [
        ("ix_admin_audit_logs_org_created", ["org_id", "created_at"]),
    ],
    "export_events": [
        ("ix_export_events_org_created", ["org_id", "created_at"]),
    ],
    "document_templates": [
        ("ix_document_templates_org_type", ["org_id", "document_type"]),
    ],
    "documents": [
        ("ix_documents_org_type", ["org_id", "document_type"]),
    ],
}


def _ensure_default_org() -> None:
    conn = op.get_bind()
    default_id = str(DEFAULT_ORG_ID)
    existing = conn.execute(
        sa.text("SELECT org_id FROM organizations WHERE org_id = :org_id"),
        {"org_id": default_id},
    ).fetchone()
    if existing:
        return

    conn.execute(
        sa.text(
            "INSERT INTO organizations (org_id, name) VALUES (:org_id, :name) "
            "ON CONFLICT (org_id) DO NOTHING"
        ),
        {"org_id": default_id, "name": DEFAULT_ORG_NAME},
    )


def _add_org_id_column(table: str) -> None:
    dialect = op.get_bind().dialect.name
    column = sa.Column(
        "org_id",
        UUID_TYPE,
        nullable=True,
        server_default=sa.text(f"'{DEFAULT_ORG_ID}'"),
    )
    fk_name = f"fk_{table}_org_id_organizations"
    if dialect == "sqlite":
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(column)
            batch_op.create_foreign_key(
                fk_name,
                "organizations",
                ["org_id"],
                ["org_id"],
                ondelete="CASCADE",
            )
    else:
        op.add_column(table, column)
        op.create_foreign_key(
            fk_name,
            table,
            "organizations",
            ["org_id"],
            ["org_id"],
            ondelete="CASCADE",
        )


def _backfill(table: str) -> None:
    stmt = sa.text(f"UPDATE {table} SET org_id = :org_id WHERE org_id IS NULL")
    op.execute(stmt.bindparams(org_id=str(DEFAULT_ORG_ID)))


def _finalize_column(table: str) -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(
                "org_id", existing_type=UUID_TYPE, server_default=None, nullable=False
            )
    else:
        op.alter_column(table, "org_id", server_default=None, nullable=False)


def _create_indexes(table: str) -> None:
    for name, columns in TABLE_INDEXES.get(table, []):
        op.create_index(name, table, columns)


def _drop_indexes(table: str) -> None:
    for name, _ in TABLE_INDEXES.get(table, []):
        op.drop_index(name, table_name=table)


TABLES = [
    "teams",
    "team_working_hours",
    "team_blackouts",
    "bookings",
    "email_events",
    "order_photos",
    "chat_sessions",
    "leads",
    "referral_credits",
    "invoices",
    "invoice_items",
    "invoice_payments",
    "invoice_public_tokens",
    "workers",
    "subscriptions",
    "subscription_addons",
    "disputes",
    "financial_adjustment_events",
    "admin_audit_logs",
    "export_events",
    "document_templates",
    "documents",
]


def upgrade() -> None:
    _ensure_default_org()

    for table in TABLES:
        _add_org_id_column(table)
        _backfill(table)
        _finalize_column(table)
        _create_indexes(table)


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    for table in reversed(TABLES):
        _drop_indexes(table)
        if dialect == "sqlite":
            with op.batch_alter_table(table) as batch_op:
                batch_op.drop_constraint(
                    f"fk_{table}_org_id_organizations", type_="foreignkey"
                )
                batch_op.drop_column("org_id")
        else:
            op.drop_constraint(
                f"fk_{table}_org_id_organizations", table_name=table, type_="foreignkey"
            )
            op.drop_column(table, "org_id")
