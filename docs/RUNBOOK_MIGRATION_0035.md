# Runbook: Migration 0035 - Add org_id to Core Tables

**Migration**: `0035_add_org_id_to_core_tables`
**Type**: Schema change (staged migration with backfill)
**Risk Level**: Medium (adds columns and indexes, minimal downtime expected)
**Estimated Duration**: 5-15 minutes (depends on table sizes)
**Rollback**: Supported via Alembic downgrade (see Rollback section)

## Overview

This migration adds `org_id` column to 30+ core business tables to enable multi-tenant data isolation. The migration follows a staged approach to minimize downtime:

1. Add `org_id` column (nullable, with server default to DEFAULT_ORG_ID)
2. Backfill any NULL values
3. **Drop server default** to prevent silent fallback to DEFAULT_ORG_ID
4. Set NOT NULL constraint
5. Add foreign key constraints to `organizations` table (NO CASCADE delete)
6. Create indexes (single and composite) for query performance

## Pre-Migration Checklist

### 1. Verify Prerequisites

```bash
# Ensure migration 0034 has been applied
alembic current

# Expected output should show:
# 0034_org_id_uuid_and_default_org (head)
```

### 2. Verify Required Core Tables Exist

**IMPORTANT**: Migration 0035 will fail fast if any of these required tables are missing:
- `teams`
- `bookings`
- `leads`
- `invoices`
- `workers`

```sql
-- Verify required tables exist
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('teams', 'bookings', 'leads', 'invoices', 'workers')
ORDER BY table_name;

-- Expected: 5 rows (all required tables)
```

If any required tables are missing, the migration will fail with a clear error message. This is intentional to prevent incomplete multi-tenant setup.

### 3. Verify Default Organization Exists

```sql
-- Connect to your database and run:
SELECT org_id, name FROM organizations WHERE org_id = '00000000-0000-0000-0000-000000000001';

-- Expected output:
-- org_id: 00000000-0000-0000-0000-000000000001
-- name: Default Org
```

If the default org doesn't exist, run migration 0034 first:
```bash
alembic upgrade 0034
```

### 4. Database Backup

**CRITICAL**: Always create a backup before running schema migrations.

```bash
# PostgreSQL backup example
pg_dump -h <host> -U <user> -d <database> -F c -f backup_pre_migration_0035_$(date +%Y%m%d_%H%M%S).dump

# Verify backup was created
ls -lh backup_pre_migration_0035_*.dump
```

### 5. Estimate Migration Duration

The migration duration depends on the number of rows in your tables. Use this query to estimate:

```sql
-- Get row counts for the largest tables
SELECT
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size,
    n_live_tup AS estimated_rows
FROM pg_stat_user_tables
WHERE tablename IN (
    'bookings', 'invoices', 'leads', 'workers', 'teams',
    'invoice_payments', 'email_events', 'subscriptions',
    'disputes', 'admin_audit_logs', 'documents'
)
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
```

**Rough estimates**:
- < 100K rows total: 1-2 minutes
- 100K - 500K rows: 3-5 minutes
- 500K - 1M rows: 5-10 minutes
- > 1M rows: 10-15 minutes

### 6. Plan Maintenance Window (Optional)

While the migration is designed to run with minimal downtime, consider scheduling during low-traffic periods for large deployments.

**Recommended**: Run during off-peak hours (e.g., 2-4 AM local time)

## Migration Execution

### Step 1: Put Application in Maintenance Mode (Optional)

If running during business hours or with very large datasets:

```bash
# Set maintenance mode (application-specific)
# Example: Set env var or feature flag
export MAINTENANCE_MODE=true

# Or use a reverse proxy to show maintenance page
```

### Step 2: Stop Application Services (Optional)

For critical production systems, stop application services to prevent writes during migration:

```bash
# Example using systemd
sudo systemctl stop your-app-service

# Or Docker
docker-compose stop app worker

# Or Kubernetes
kubectl scale deployment your-app --replicas=0
```

### Step 3: Run Migration

```bash
# Navigate to project root
cd /path/to/Clean

# Activate virtual environment if needed
source venv/bin/activate  # or your venv path

# Run migration with verbose output
alembic upgrade head

# Monitor output for any errors
# Expected output includes:
# ✓ Adding org_id column to <table>
# ✓ Backfilling org_id in <table>
# ✓ Dropping server_default on <table>.org_id
# ✓ Setting NOT NULL constraint on <table>.org_id
# ✓ Adding FK constraint to <table>
# ✓ Creating index ix_<table>_org_id
# ✓ Creating composite index ix_<table>_org_<column>
# ✅ Migration complete: org_id added to all core tables
```

### Step 4: Verify Migration Success

```bash
# Check current migration version
alembic current

# Expected output:
# 0035_add_org_id_to_core_tables (head)
```

### Step 5: Database Verification

Run these SQL queries to verify the migration:

```sql
-- 1. Check that org_id column exists in core tables
SELECT
    table_name,
    column_name,
    data_type,
    is_nullable
FROM information_schema.columns
WHERE column_name = 'org_id'
  AND table_schema = 'public'
ORDER BY table_name;

-- Expected: 30+ rows, all with data_type='uuid', is_nullable='NO'

-- 2. Verify server_default was dropped (no silent fallback)
SELECT
    table_name,
    column_name,
    column_default
FROM information_schema.columns
WHERE column_name = 'org_id'
  AND table_schema = 'public'
  AND table_name IN ('bookings', 'invoices', 'leads', 'teams', 'workers')
ORDER BY table_name;

-- Expected: column_default should be NULL for all tables
-- This ensures new inserts without org_id will fail, not silently default

-- 3. Verify all rows have org_id set to default org
SELECT
    'bookings' AS table_name,
    COUNT(*) AS total_rows,
    COUNT(org_id) AS rows_with_org_id,
    COUNT(DISTINCT org_id) AS distinct_org_ids
FROM bookings
UNION ALL
SELECT 'invoices', COUNT(*), COUNT(org_id), COUNT(DISTINCT org_id) FROM invoices
UNION ALL
SELECT 'leads', COUNT(*), COUNT(org_id), COUNT(DISTINCT org_id) FROM leads
UNION ALL
SELECT 'teams', COUNT(*), COUNT(org_id), COUNT(DISTINCT org_id) FROM teams;

-- Expected: total_rows = rows_with_org_id for all tables
-- Expected: distinct_org_ids = 1 (only default org)

-- 4. Verify foreign key constraints exist (no CASCADE delete)
SELECT
    tc.table_name,
    tc.constraint_name,
    tc.constraint_type,
    kcu.column_name,
    ccu.table_name AS foreign_table_name,
    ccu.column_name AS foreign_column_name
FROM information_schema.table_constraints AS tc
JOIN information_schema.key_column_usage AS kcu
  ON tc.constraint_name = kcu.constraint_name
JOIN information_schema.constraint_column_usage AS ccu
  ON ccu.constraint_name = tc.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY'
  AND kcu.column_name = 'org_id'
  AND ccu.table_name = 'organizations'
ORDER BY tc.table_name;

-- Expected: 30+ rows showing FK constraints from each table to organizations
-- NOTE: This migration does NOT use CASCADE delete for safety

-- 5. Verify indexes were created
SELECT
    schemaname,
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE indexname LIKE '%_org_%'
  AND schemaname = 'public'
ORDER BY tablename, indexname;

-- Expected: Multiple indexes including:
--   - ix_<table>_org_id (single column)
--   - ix_<table>_org_<column> (composite indexes)
```

### Step 6: Restart Application Services

```bash
# Example using systemd
sudo systemctl start your-app-service

# Or Docker
docker-compose up -d app worker

# Or Kubernetes
kubectl scale deployment your-app --replicas=3  # your desired replica count
```

### Step 7: Remove Maintenance Mode

```bash
# Unset maintenance mode
unset MAINTENANCE_MODE

# Or restore normal traffic via reverse proxy
```

## Post-Migration Verification

### 1. Smoke Tests

Run these tests to ensure the application works correctly:

```bash
# Run automated test suite
pytest -q

# Expected: All tests pass
```

### 2. Manual Verification

1. **Login Test**: Login as an admin user
2. **Create Test**: Create a new booking/lead/invoice
3. **Query Test**: List bookings/leads/invoices (should show both old and new data)
4. **Org Isolation Test**: If you have multiple orgs, verify data isolation

### 3. Check org_id in New Records

```sql
-- Create a test booking/lead/invoice via the application
-- Then verify it has org_id set

SELECT booking_id, org_id, status, created_at
FROM bookings
ORDER BY created_at DESC
LIMIT 5;

-- Expected: org_id should be populated for new records
```

### 4. Monitor Application Logs

```bash
# Check for any database errors or warnings
tail -f /var/log/your-app/app.log

# Look for:
# - Any SQL errors related to org_id
# - Performance issues with queries
# - Foreign key constraint violations
```

### 5. Monitor Database Performance

```sql
-- Check for slow queries
SELECT
    query,
    calls,
    total_time,
    mean_time,
    max_time
FROM pg_stat_statements
WHERE query LIKE '%org_id%'
ORDER BY mean_time DESC
LIMIT 10;

-- Monitor index usage
SELECT
    schemaname,
    tablename,
    indexname,
    idx_scan,
    idx_tup_read,
    idx_tup_fetch
FROM pg_stat_user_indexes
WHERE indexname LIKE '%_org_%'
ORDER BY idx_scan DESC;
```

## Rollback Procedure

If you encounter issues and need to rollback:

### Step 1: Stop Application Services

```bash
# Stop application to prevent writes
sudo systemctl stop your-app-service
```

### Step 2: Downgrade Migration

```bash
# Downgrade to migration 0034
alembic downgrade -1

# Monitor output for any errors
# Expected output includes:
# ✓ Dropping index <index_name>
# ✓ Dropping FK constraint <fk_name>
# ✓ Dropping org_id column from <table>
# ✅ Downgrade complete: org_id removed from all core tables
```

### Step 3: Verify Rollback

```sql
-- Verify org_id column no longer exists
SELECT table_name, column_name
FROM information_schema.columns
WHERE column_name = 'org_id'
  AND table_schema = 'public'
  AND table_name NOT IN ('organizations', 'memberships', 'api_tokens', 'organization_billing', 'organization_usage_events');

-- Expected: 0 rows (org_id should only exist in pre-existing SaaS tables)
```

### Step 4: Restart Application

```bash
sudo systemctl start your-app-service
```

### Step 5: Restore from Backup (If Necessary)

If rollback fails or data corruption occurs:

```bash
# Stop application
sudo systemctl stop your-app-service

# Restore from backup
pg_restore -h <host> -U <user> -d <database> -c backup_pre_migration_0035_*.dump

# Restart application
sudo systemctl start your-app-service
```

## Troubleshooting

### Issue: Migration Hangs on Large Table

**Symptom**: Migration appears stuck on a particular table

**Solution**:
1. Check database locks: `SELECT * FROM pg_locks WHERE NOT granted;`
2. Consider running migration during maintenance window
3. For very large tables (>10M rows), consider manual chunked migration

### Issue: Foreign Key Constraint Violation

**Symptom**: Error like "violates foreign key constraint"

**Cause**: Default organization doesn't exist

**Solution**:
```sql
-- Insert default organization manually
INSERT INTO organizations (org_id, name)
VALUES ('00000000-0000-0000-0000-000000000001', 'Default Org')
ON CONFLICT (org_id) DO NOTHING;

-- Then retry migration
alembic upgrade head
```

### Issue: Index Creation Timeout

**Symptom**: Timeout during index creation on large tables

**Solution**:
```sql
-- Increase statement timeout temporarily
SET statement_timeout = '30min';

-- Then retry migration
```

### Issue: Future Unique Constraint Conflicts (Multi-Tenant Data)

**Note**: Migration 0035 itself does NOT modify unique constraints. This issue only applies when you start using multiple organizations.

**Symptom**: After migration, when creating data for multiple orgs, you may see unique constraint violations

**Cause**: Some tables have unique constraints that don't include org_id (e.g., `teams.name` is unique globally, not per-org)

**Solution**: The schema migration (0035) completes successfully. However, for proper multi-tenant isolation, Sprint 3/4 will update unique constraints to include org_id scope (e.g., `UNIQUE(org_id, name)` instead of `UNIQUE(name)`).

## Performance Considerations

### Index Usage

The migration creates composite indexes optimized for common query patterns:

- `(org_id, status)` - For filtering by status within an organization
- `(org_id, created_at)` - For time-based queries within an organization
- `(org_id, <foreign_key>)` - For joins within an organization

### Query Performance Impact

- **Reads**: Minimal impact. Queries will use composite indexes efficiently.
- **Writes**: Slight overhead from additional index maintenance (typically < 5%)

### Storage Impact

Estimate additional storage:
- org_id column: 16 bytes per row
- Indexes: ~2x the size of org_id column data

For 1M total rows: ~48 MB additional storage

## Next Steps

After successful migration:

1. **Sprint 3**: Update application code to enforce org_id filtering in all queries
2. **Sprint 4**: Update unique constraints to include org_id for proper isolation
3. **Monitor**: Watch for slow queries and optimize indexes as needed
4. **Document**: Update API documentation to reflect org_id requirements

## Support

If you encounter issues not covered in this runbook:

1. Check application logs for detailed error messages
2. Review Alembic migration output for specific failure points
3. Consult database logs for constraint violations or deadlocks
4. Contact the database team for assistance

## Appendix: Tables Modified

This migration adds `org_id` to the following 30+ tables:

**Bookings Domain**:
- teams
- bookings
- email_events
- order_photos
- team_working_hours
- team_blackouts

**Leads Domain**:
- chat_sessions
- leads
- referral_credits

**Invoices Domain**:
- invoice_number_sequences
- invoices
- invoice_items
- invoice_payments
- stripe_events
- invoice_public_tokens

**Workers Domain**:
- workers

**Documents Domain**:
- document_templates
- documents

**Subscriptions Domain**:
- subscriptions
- subscription_addons

**Disputes Domain**:
- disputes
- financial_adjustment_events

**Admin/Audit Domain**:
- admin_audit_logs

**Checklists Domain**:
- checklist_templates
- checklist_template_items
- checklist_runs
- checklist_run_items

**Addons Domain**:
- addon_definitions
- order_addons

**Clients Domain**:
- client_users

---

**Document Version**: 1.0
**Last Updated**: 2025-12-31
**Author**: Backend Data Engineering Team
