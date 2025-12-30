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

Uploads live under `ORDER_UPLOAD_ROOT` (default `var/uploads/orders`). Production should mount this directory on durable, encrypted storage (e.g., block storage or an S3-compatible bucket via object-storage mount).

Strategy:

- Nightly sync to object storage with versioning enabled.
- Exclude transient temp files; include original photos and generated PDFs.
- Retain at least 30 days of versions; use lifecycle rules to expire older copies.

Example `rsync` from the app host to a mounted backup volume:

```bash
rsync -av --delete /srv/clean/var/uploads/orders /mnt/backup/clean/uploads
```

Restore by syncing the desired snapshot back to the application mount, then invalidate any CDN cache that serves uploaded files.
