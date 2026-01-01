# Backup and restore

This is the production-facing reference for database and storage backups. For operational drills see `scripts/chaos/` and `docs/prod_cutover_checklist.md`.

## Postgres backups

Use logical dumps so restores can be replayed into new instances.

```bash
# Full logical backup with compression
PGPASSWORD="$POSTGRES_PASSWORD" pg_dump \
  --format=custom \
  --file=cleaning_$(date +%Y%m%d).dump \
  --host=$POSTGRES_HOST \
  --port=${POSTGRES_PORT:-5432} \
  --username=$POSTGRES_USER \
  $POSTGRES_DB
```

Recommendations:

- Run during low traffic windows; enable `statement_timeout` to avoid long locks.
- Store dumps in an encrypted, versioned bucket. Keep at least 7 daily + 4 weekly snapshots.
- Record the Alembic head (`alembic current`) in the backup metadata.

### Restore

```bash
PGPASSWORD="$POSTGRES_PASSWORD" pg_restore \
  --clean --if-exists --create \
  --dbname=postgres \
  --host=$POSTGRES_HOST \
  --port=${POSTGRES_PORT:-5432} \
  --username=$POSTGRES_USER \
  cleaning_20250101.dump
```

After restore:

1. Run `alembic upgrade head` if needed.
2. Run `pytest -q` against the restored DB in staging.
3. Smoke test bookings, invoices, and webhook retries.

## Object storage backups

When `ORDER_STORAGE_BACKEND=s3`, enable bucket versioning and server-side encryption. Apply lifecycle rules:

- Keep 30 days of non-current versions; transition older versions to IA/Glacier tiers as appropriate.
- Expire delete markers after 30 days.

Restore by promoting the desired version (or copying it back). Invalidate CDN caches after restore.

For `ORDER_STORAGE_BACKEND=local` (dev/stage only), sync the `uploads/` path to a versioned bucket nightly.

## Recovery drills

- Quarterly: perform a full restore into staging from production backups and run `scripts/load/saas_k6.js` with low VUs to confirm integrity.
- Track RPO/RTO outcomes in the incident tracker; update runbooks if objectives are missed.
