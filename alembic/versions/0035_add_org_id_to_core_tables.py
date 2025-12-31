"""
Add org_id to core business tables for multi-tenant isolation.

Revision ID: 0035_add_org_id_to_core_tables
Revises: 0034_org_id_uuid_and_default_org
Create Date: 2025-12-31
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = "0035_add_org_id_to_core_tables"
down_revision = "0034_org_id_uuid_and_default_org"
branch_labels = None
depends_on = None

UUID_TYPE = sa.Uuid(as_uuid=True)
DEFAULT_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


# Define all core tables that need org_id
# Organized by domain for clarity
CORE_TABLES = [
    # Bookings domain
    "teams",
    "bookings",
    "email_events",
    "order_photos",
    "team_working_hours",
    "team_blackouts",
    # Leads domain
    "chat_sessions",
    "leads",
    "referral_credits",
    # Invoices domain
    "invoice_number_sequences",
    "invoices",
    "invoice_items",
    "invoice_payments",
    "stripe_events",
    "invoice_public_tokens",
    # Workers domain
    "workers",
    # Documents domain
    "document_templates",
    "documents",
    # Subscriptions domain
    "subscriptions",
    "subscription_addons",
    # Disputes domain
    "disputes",
    "financial_adjustment_events",
    # Admin audit domain
    "admin_audit_logs",
    # Checklists domain
    "checklist_templates",
    "checklist_template_items",
    "checklist_runs",
    "checklist_run_items",
    # Addons domain
    "addon_definitions",
    "order_addons",
    # Clients domain
    "client_users",
]


# Define composite indexes for efficient multi-tenant queries
# Format: {table: [(columns, index_name), ...]}
COMPOSITE_INDEXES = {
    "teams": [
        (["org_id", "created_at"], "ix_teams_org_created"),
    ],
    "bookings": [
        (["org_id", "status"], "ix_bookings_org_status"),
        (["org_id", "starts_at"], "ix_bookings_org_starts"),
        (["org_id", "client_id"], "ix_bookings_org_client"),
    ],
    "team_working_hours": [
        (["org_id", "team_id"], "ix_team_working_hours_org_team"),
    ],
    "team_blackouts": [
        (["org_id", "team_id"], "ix_team_blackouts_org_team"),
    ],
    "leads": [
        (["org_id", "status"], "ix_leads_org_status"),
        (["org_id", "created_at"], "ix_leads_org_created"),
    ],
    "invoice_number_sequences": [
        # CRITICAL: Prevent invoice number conflicts across orgs
        (["org_id", "year"], "ix_invoice_number_sequences_org_year"),
    ],
    "invoices": [
        (["org_id", "status"], "ix_invoices_org_status"),
        (["org_id", "created_at"], "ix_invoices_org_created"),
    ],
    "invoice_payments": [
        (["org_id", "status"], "ix_invoice_payments_org_status"),
        (["org_id", "created_at"], "ix_invoice_payments_org_created"),
    ],
    "workers": [
        (["org_id", "team_id"], "ix_workers_org_team"),
        (["org_id", "is_active"], "ix_workers_org_active"),
    ],
    "document_templates": [
        (["org_id", "document_type"], "ix_document_templates_org_type"),
    ],
    "documents": [
        (["org_id", "reference_id"], "ix_documents_org_reference"),
        (["org_id", "document_type"], "ix_documents_org_type"),
    ],
    "subscriptions": [
        (["org_id", "client_id"], "ix_subscriptions_org_client"),
        (["org_id", "status"], "ix_subscriptions_org_status"),
    ],
    "disputes": [
        (["org_id", "state"], "ix_disputes_org_state"),
    ],
    "financial_adjustment_events": [
        (["org_id", "created_at"], "ix_financial_adjustment_events_org_created"),
    ],
    "admin_audit_logs": [
        (["org_id", "created_at"], "ix_admin_audit_logs_org_created"),
        (["org_id", "resource_type"], "ix_admin_audit_logs_org_resource_type"),
    ],
    "checklist_templates": [
        (["org_id", "service_type"], "ix_checklist_templates_org_service_type"),
    ],
    "checklist_runs": [
        (["org_id", "order_id"], "ix_checklist_runs_org_order"),
    ],
    "client_users": [
        (["org_id", "email"], "ix_client_users_org_email"),
    ],
}


def _table_exists(conn, table_name: str) -> bool:
    """Check if a table exists in the database."""
    result = conn.execute(
        sa.text(
            """
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_name = :table
            )
            """
        ),
        {"table": table_name},
    )
    return result.scalar() or False


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    result = conn.execute(
        sa.text(
            """
            SELECT EXISTS (
                SELECT FROM information_schema.columns
                WHERE table_schema = 'public'
                AND table_name = :table
                AND column_name = :column
            )
            """
        ),
        {"table": table_name, "column": column_name},
    )
    return result.scalar() or False


def _index_exists(conn, index_name: str) -> bool:
    """Check if an index exists."""
    result = conn.execute(
        sa.text(
            """
            SELECT EXISTS (
                SELECT FROM pg_indexes
                WHERE schemaname = 'public'
                AND indexname = :index
            )
            """
        ),
        {"index": index_name},
    )
    return result.scalar() or False


def upgrade() -> None:
    conn = op.get_bind()

    # Step 1: Add org_id column to all core tables (nullable with default)
    for table in CORE_TABLES:
        if not _table_exists(conn, table):
            print(f"⚠️  Table {table} does not exist, skipping...")
            continue

        if _column_exists(conn, table, "org_id"):
            print(f"ℹ️  Column org_id already exists in {table}, skipping add...")
            continue

        print(f"✓ Adding org_id column to {table}")
        op.add_column(
            table,
            sa.Column(
                "org_id",
                UUID_TYPE,
                nullable=True,
                server_default=sa.text(f"'{DEFAULT_ORG_ID}'::uuid"),
            ),
        )

    # Step 2: Backfill NULL values (belt and suspenders approach)
    for table in CORE_TABLES:
        if not _table_exists(conn, table):
            continue

        if not _column_exists(conn, table, "org_id"):
            continue

        print(f"✓ Backfilling org_id in {table}")
        conn.execute(
            sa.text(
                f"""
                UPDATE {table}
                SET org_id = :default_org_id
                WHERE org_id IS NULL
                """
            ),
            {"default_org_id": str(DEFAULT_ORG_ID)},
        )

    # Step 3: Set NOT NULL constraint
    for table in CORE_TABLES:
        if not _table_exists(conn, table):
            continue

        if not _column_exists(conn, table, "org_id"):
            continue

        print(f"✓ Setting NOT NULL constraint on {table}.org_id")
        op.alter_column(
            table,
            "org_id",
            nullable=False,
            existing_type=UUID_TYPE,
        )

    # Step 4: Add foreign key constraints
    for table in CORE_TABLES:
        if not _table_exists(conn, table):
            continue

        if not _column_exists(conn, table, "org_id"):
            continue

        print(f"✓ Adding FK constraint to {table}")
        op.create_foreign_key(
            f"fk_{table}_org_id_organizations",
            table,
            "organizations",
            ["org_id"],
            ["org_id"],
            ondelete="CASCADE",
        )

    # Step 5: Add single-column indexes on org_id
    for table in CORE_TABLES:
        if not _table_exists(conn, table):
            continue

        if not _column_exists(conn, table, "org_id"):
            continue

        # Skip if composite index will cover it
        if table in COMPOSITE_INDEXES:
            # Check if any composite index starts with org_id
            has_org_id_first = any(
                cols[0] == "org_id" for cols, _ in COMPOSITE_INDEXES[table]
            )
            if has_org_id_first:
                print(f"ℹ️  Skipping single index for {table} (covered by composite)")
                continue

        index_name = f"ix_{table}_org_id"
        if _index_exists(conn, index_name):
            print(f"ℹ️  Index {index_name} already exists, skipping...")
            continue

        print(f"✓ Creating index {index_name}")
        op.create_index(index_name, table, ["org_id"])

    # Step 6: Add composite indexes for efficient multi-tenant queries
    for table, indexes in COMPOSITE_INDEXES.items():
        if not _table_exists(conn, table):
            continue

        for columns, index_name in indexes:
            if _index_exists(conn, index_name):
                print(f"ℹ️  Index {index_name} already exists, skipping...")
                continue

            print(f"✓ Creating composite index {index_name} on {table}")
            op.create_index(index_name, table, columns)

    print("✅ Migration complete: org_id added to all core tables")


def downgrade() -> None:
    """
    Downgrade migration: Remove org_id columns and related constraints.

    WARNING: This is a destructive operation that will remove multi-tenant
    isolation. Only run this if you are certain you want to revert to
    single-tenant mode.
    """
    conn = op.get_bind()

    # Step 1: Drop composite indexes
    for table, indexes in COMPOSITE_INDEXES.items():
        if not _table_exists(conn, table):
            continue

        for _, index_name in indexes:
            if not _index_exists(conn, index_name):
                continue

            print(f"✓ Dropping index {index_name}")
            op.drop_index(index_name, table_name=table)

    # Step 2: Drop single-column indexes
    for table in CORE_TABLES:
        if not _table_exists(conn, table):
            continue

        index_name = f"ix_{table}_org_id"
        if not _index_exists(conn, index_name):
            continue

        print(f"✓ Dropping index {index_name}")
        op.drop_index(index_name, table_name=table)

    # Step 3: Drop foreign key constraints
    for table in CORE_TABLES:
        if not _table_exists(conn, table):
            continue

        if not _column_exists(conn, table, "org_id"):
            continue

        fk_name = f"fk_{table}_org_id_organizations"
        print(f"✓ Dropping FK constraint {fk_name}")
        op.drop_constraint(fk_name, table_name=table, type_="foreignkey")

    # Step 4: Drop org_id columns
    for table in CORE_TABLES:
        if not _table_exists(conn, table):
            continue

        if not _column_exists(conn, table, "org_id"):
            continue

        print(f"✓ Dropping org_id column from {table}")
        op.drop_column(table, "org_id")

    print("✅ Downgrade complete: org_id removed from all core tables")
