# Org ID core-tables migration runbook (Sprint 2)

## Scope
Adds `org_id` (UUID) to core business tables and backfills existing data to the fixed `DEFAULT_ORG_ID` (`00000000-0000-0000-0000-000000000001`). Application code for strict org scoping lands in later sprints; this runbook focuses on schema safety.

## Pre-checks
1. Confirm backup is recent (logical dump or snapshot) and note restore steps.
2. Verify migration head is available: `alembic history | tail` should show `0035_core_tables_org_id`.
3. Ensure the `organizations` table contains the default org:
   ```sql
   SELECT org_id, name FROM organizations WHERE org_id = '00000000-0000-0000-0000-000000000001';
   ```
4. Pause writes if possible (short maintenance window) to avoid concurrent inserts without org_id while the columns are nullable.

## Execution
1. Run migrations:
   ```bash
   alembic upgrade 0035_core_tables_org_id
   ```
2. Observe logs for lock contention; the migration stages columns as nullable with a temporary default, backfills, then enforces `NOT NULL` with FKs and indexes.
3. Validate post-state with spot checks:
   ```sql
   -- bookings
   
   SELECT COUNT(*) FROM bookings WHERE org_id IS NULL;
   -- invoices
   SELECT COUNT(*) FROM invoices WHERE org_id IS NULL;
   -- disputes
   SELECT COUNT(*) FROM disputes WHERE org_id IS NULL;
   ```
4. Verify indexes exist for hot paths:
   ```sql
   \d+ bookings
   \d+ invoice_payments
   \d+ admin_audit_logs
   ```
5. Resume traffic and monitor error logs for missing `org_id` writes. For SaaS traffic, require org_id in requests; do not rely on silent defaults.

## Rollback / Downgrade
1. If issues surface immediately and code can tolerate missing org_id columns, run:
   ```bash
   alembic downgrade 0034_org_id_uuid_and_default_org
   ```
2. Validate that application code no longer expects `org_id` in the dropped tables before re-enabling traffic.
3. If data was written during the window, reconcile manually after restoring from backup or re-running the migration with corrected code.

## Notes
- The migration recreates a default org/billing row if missing; this is idempotent and safe on empty databases.
- Keep `DEFAULT_ORG_ID` consistent across environments to avoid cross-tenant leakage when Sprint 3 enforcement is added.
