# Postgres RLS rollout runbook

This runbook guides operators through enabling and validating the Postgres row-level security (RLS) rollout that isolates tenant data by `org_id`.

## Preconditions
- Postgres is running and reachable with migration permissions.
- Migration `0044_postgres_rls_org_isolation` has been applied.
- You are connected with the application role (non-superuser) so policy checks are enforced.

## Enable and verify RLS policies
1. Confirm RLS policies are present on org-scoped tables:
   ```sql
   SELECT tablename, polname, roles, cmd, qual, with_check
   FROM pg_policies
   WHERE polname LIKE '%org_isolation%'
   ORDER BY tablename;
   ```
   Ensure `FORCE ROW LEVEL SECURITY` is enabled and that every table listed in the migration (leads, bookings, invoices, invoice_payments, workers, teams, order_photos, export_events, email_events) has an `_org_isolation` policy.
2. Verify the session variable is wired by the application middleware using `SET LOCAL app.current_org_id` inside a transaction:
   ```sql
   BEGIN;
   SET LOCAL app.current_org_id = '<org_uuid>';
   SELECT COUNT(*) FROM leads;
   COMMIT;
   ```
   The count should reflect only rows for the chosen org. Repeat with a different org UUID to confirm isolation.

## Validate behavior when org context is missing
- In a new transaction, do **not** set `app.current_org_id` and query a protected table:
  ```sql
  BEGIN;
  SELECT * FROM leads LIMIT 5;
  COMMIT;
  ```
  Expect zero rows because the policy filters on `app.current_org_id`.
- Attempting to insert without `SET LOCAL app.current_org_id` should fail with a policy error:
  ```sql
  BEGIN;
  INSERT INTO leads (lead_id, org_id, name) VALUES ('<uuid>', '<org_uuid>', 'RLS check');
  COMMIT;
  ```
  The statement is blocked because the policy’s `WITH CHECK` clause requires the session variable to match `org_id`. Retry after setting `SET LOCAL app.current_org_id = '<org_uuid>'` to observe a successful insert.

## Background jobs and scripts
- Background jobs, admin scripts, and data-repair tasks **must set the org context** before reading or writing to any RLS-protected table. The application sets `SET LOCAL app.current_org_id` automatically per request; out-of-band jobs must do the same inside each transaction.
- Missing org context will silently return empty result sets and reject writes, which can cause subtle data loss during migrations or batch tasks. Always set the org ID explicitly and validate the expected row counts before committing.
