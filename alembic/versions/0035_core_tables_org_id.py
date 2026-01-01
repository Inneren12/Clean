"""
Add org_id to core business tables with backfill and indexes.

Revision ID: 0035_core_tables_org_id
Revises: 0034_org_id_uuid_and_default_org
Create Date: 2025-06-01
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = "0035_core_tables_org_id"
down_revision = "0034_org_id_uuid_and_default_org"
branch_labels = None
depends_on = None

UUID_TYPE = sa.Uuid(as_uuid=True)
DEFAULT_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
DEFAULT_ORG_NAME = "Default Org"

TABLES = [
    "teams",
    "bookings",
    "leads",
    "invoices",
    "invoice_payments",
    "workers",
    "documents",
    "order_photos",
    "subscriptions",
    "disputes",
    "financial_adjustment_events",
    "admin_audit_logs",
    "export_events",
    "email_events",
]

INDEXES: dict[str, tuple[tuple[str, list[str]], ...]] = {
    "teams": (("ix_teams_org_id", ["org_id"],),),
    "bookings": (
        ("ix_bookings_org_id", ["org_id"]),
        ("ix_bookings_org_status", ["org_id", "status"]),
        ("ix_bookings_org_created_at", ["org_id", "created_at"]),
        ("ix_bookings_org_starts_at", ["org_id", "starts_at"]),
    ),
    "leads": (
        ("ix_leads_org_id", ["org_id"]),
        ("ix_leads_org_status", ["org_id", "status"]),
        ("ix_leads_org_created_at", ["org_id", "created_at"]),
    ),
    "invoices": (
        ("ix_invoices_org_id", ["org_id"]),
        ("ix_invoices_org_status", ["org_id", "status"]),
        ("ix_invoices_org_created_at", ["org_id", "created_at"]),
    ),
    "invoice_payments": (
        ("ix_invoice_payments_org_id", ["org_id"]),
        ("ix_invoice_payments_org_status", ["org_id", "status"]),
    ),
    "workers": (
        ("ix_workers_org_id", ["org_id"]),
        ("ix_workers_org_active", ["org_id", "is_active"]),
    ),
    "documents": (
        ("ix_documents_org_id", ["org_id"]),
        ("ix_documents_org_type", ["org_id", "document_type"]),
    ),
    "order_photos": (
        ("ix_order_photos_org_id", ["org_id"]),
        ("ix_order_photos_org_order", ["org_id", "order_id"]),
    ),
    "subscriptions": (
        ("ix_subscriptions_org_id", ["org_id"]),
        ("ix_subscriptions_org_status", ["org_id", "status"]),
        ("ix_subscriptions_org_created_at", ["org_id", "created_at"]),
    ),
    "disputes": (
        ("ix_disputes_org_id", ["org_id"]),
        ("ix_disputes_org_state", ["org_id", "state"]),
    ),
    "financial_adjustment_events": (
        ("ix_financial_events_org_id", ["org_id"]),
        ("ix_financial_events_org_created", ["org_id", "created_at"]),
    ),
    "admin_audit_logs": (
        ("ix_admin_audit_logs_org_id", ["org_id"]),
        ("ix_admin_audit_logs_org_created", ["org_id", "created_at"]),
    ),
    "export_events": (
        ("ix_export_events_org_id", ["org_id"]),
        ("ix_export_events_org_created", ["org_id", "created_at"]),
    ),
    "email_events": (
        ("ix_email_events_org_id", ["org_id"]),
        ("ix_email_events_org_created_at", ["org_id", "created_at"]),
    ),
}


def _ensure_default_org(conn: sa.engine.Connection) -> None:
    conn.execute(
        sa.text(
            """
            INSERT INTO organizations (org_id, name)
            VALUES (:org_id, :name)
            ON CONFLICT (org_id) DO NOTHING
            """
        ),
        {"org_id": str(DEFAULT_ORG_ID), "name": DEFAULT_ORG_NAME},
    )
    conn.execute(
        sa.text(
            """
            INSERT INTO organization_billing (org_id, plan_id, status)
            VALUES (:org_id, 'free', 'inactive')
            ON CONFLICT (org_id) DO NOTHING
            """
        ),
        {"org_id": str(DEFAULT_ORG_ID)},
    )


def _add_org_id_column(table: str, is_postgres: bool) -> None:
    server_default = (
        sa.text(f"'{DEFAULT_ORG_ID}'::uuid")
        if is_postgres
        else sa.text(f"'{DEFAULT_ORG_ID}'")
    )
    op.add_column(
        table,
        sa.Column(
            "org_id",
            UUID_TYPE,
            nullable=True,
            server_default=server_default,
        ),
    )


def _backfill_org_id(conn: sa.engine.Connection, table: str) -> None:
    conn.execute(
        sa.text(f"UPDATE {table} SET org_id = :org_id WHERE org_id IS NULL"),
        {"org_id": str(DEFAULT_ORG_ID)},
    )


def _add_org_fk(table: str, is_postgres: bool) -> None:
    if not is_postgres:
        return
    op.create_foreign_key(
        f"fk_{table}_org_id_organizations",
        table,
        "organizations",
        ["org_id"],
        ["org_id"],
        ondelete="CASCADE",
    )


def _finalize_org_column(table: str, is_postgres: bool) -> None:
    if not is_postgres:
        return
    op.alter_column(table, "org_id", server_default=None, nullable=False)


def _create_indexes() -> None:
    for table, indexes in INDEXES.items():
        for name, columns in indexes:
            op.create_index(name, table, columns)


def _drop_indexes() -> None:
    for table, indexes in INDEXES.items():
        for name, _ in indexes:
            op.drop_index(name, table_name=table)


def upgrade() -> None:
    conn = op.get_bind()
    _ensure_default_org(conn)
    is_postgres = getattr(conn.engine.dialect, "name", "") == "postgresql"

    for table in TABLES:
        _add_org_id_column(table, is_postgres)

    for table in TABLES:
        _backfill_org_id(conn, table)

    for table in TABLES:
        _add_org_fk(table, is_postgres)

    for table in TABLES:
        _finalize_org_column(table, is_postgres)

    _create_indexes()


def downgrade() -> None:
    conn = op.get_bind()
    is_postgres = getattr(conn.engine.dialect, "name", "") == "postgresql"

    _drop_indexes()

    for table in reversed(TABLES):
        if is_postgres:
            op.drop_constraint(
                f"fk_{table}_org_id_organizations",
                table_name=table,
                type_="foreignkey",
            )
        op.drop_column(table, "org_id")
