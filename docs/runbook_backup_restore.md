# Backup and restore runbook

Production data is primarily stored in Postgres plus uploaded files (order photos/PDFs). This runbook covers how to create restorable backups and how to restore in an emergency.

## Postgres backups

Use `pg_dump` from a trusted bastion or inside the app container with network access to the primary database.

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

Notes:

- Run during low traffic hours to reduce lock contention.
- Store dumps in an encrypted bucket (versioned) with lifecycle rules.
- Keep at least 7 daily copies + 4 weekly copies.

### Restore

```bash
# Restore into a new database (recommended) or overwrite an empty one
PGPASSWORD="$POSTGRES_PASSWORD" pg_restore \
  --clean \
  --if-exists \
  --create \
  --dbname=postgres \
  --host=$POSTGRES_HOST \
  --port=${POSTGRES_PORT:-5432} \
  --username=$POSTGRES_USER \
  cleaning_20250101.dump
```

Validation checklist after restore:

- Run Alembic migrations (if needed) to reach the expected head revision.
- Run `pytest -q` against the restored database in a staging environment.
- Spot-check critical flows: booking creation, invoice view, webhook delivery retries.

## Uploads backup (order photos/PDFs)

Uploads now live in object storage when `ORDER_STORAGE_BACKEND=s3` (default is local filesystem for development). Production environments should use an S3-compatible bucket with **versioning enabled** so that uploads are protected automatically without relying on host-level rsync jobs.

Strategy:

- Enable bucket versioning and server-side encryption on the uploads bucket.
- Apply lifecycle rules: keep 30 days of non-current versions, expire delete markers after 30 days, and transition older versions to infrequent-access/Glacier tiers as appropriate.
- For environments still using `ORDER_STORAGE_BACKEND=local` (development only), run ad-hoc copies to a versioned bucket for recovery.

Restore by copying the desired object version back into the bucket (or by promoting a previous version). After restore, invalidate any CDN cache that serves uploaded files.
