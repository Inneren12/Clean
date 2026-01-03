# Backup and restore runbook

Production data is primarily stored in Postgres plus uploaded files (order photos/PDFs). This runbook covers how to create
restorable backups, automate them, validate restores, and define RTO/RPO targets.

## Targets and expectations
- **RPO (Recovery Point Objective): 24 hours.** A nightly logical backup is sufficient because data loss of more than one day would impact invoicing and payouts; increase cadence (e.g., every 6 hours) during peak activity.
- **RTO (Recovery Time Objective): 4 hours.** This covers time to provision a fresh database (or new managed instance), restore from the most recent dump, run migrations, and validate core flows.

## Postgres backup automation
Use the provided scripts from a trusted bastion host, CI runner, or ops VM that can reach the primary database. The scripts rely on
`pg_dump`/`pg_restore` and keep only logical backups; snapshots from a managed service can be added as a second layer but are not
required for compliance with this runbook.

### Backup script
`scripts/backup_pg.sh` creates a compressed custom-format dump (schema + data + blobs) and prunes backups older than the retention window. The dump is schema + data only (no `--create`, `--no-owner`, `--no-privileges`), so restores can target any database name without clashing on database creation or ACL ownership.

```bash
# Example (self-hosted VM or bastion): nightly cron at 02:00 UTC
# Ensure pg_dump is installed and the backup directory is on encrypted disk or mounted to S3/GS bucket via agent.
POSTGRES_HOST=db.internal \
POSTGRES_DB=cleaning \
POSTGRES_USER=backup \
POSTGRES_PASSWORD=$(pass show db/backup/password) \
BACKUP_DIR=/var/backups/clean \
RETENTION_DAYS=14 \
  ./scripts/backup_pg.sh
```

For a managed database (e.g., RDS/GCP SQL), run the same script from a CI job or maintenance VM that can reach the managed endpoint. Store dumps in a versioned bucket (S3/GS/Azure) with server-side encryption and lifecycle rules (e.g., 14 days standard, 8 weeks
in infrequent-access/Glacier). Do **not** store dumps on developer laptops.

### Scheduling options
- **Self-hosted VM:** `cron` or `systemd` timer invoking `scripts/backup_pg.sh` nightly; ensure logs are shipped to centralized logging and alerts fire on non-zero exit.
- **Managed DB with CI runner:** GitHub Actions/Buildkite scheduled workflow that exports secrets from the secret store and uploads the resulting dump to the backup bucket.

### Backup validation
After each backup job (or at least weekly):
1. Download the newest dump from the bucket/location.
2. Run a local or staging restore (see below) and execute smoke tests (Alembic migrations, basic queries) to confirm dump integrity.

### Retention strategy
- Default retention: 14 days for nightly backups (configurable via `RETENTION_DAYS`).
- Long-term: keep at least 4 weekly copies (configure lifecycle rules on the bucket rather than relying only on local pruning).
- Always enable bucket versioning + encryption.

## Postgres restore (tested path)
Restores are performed with `scripts/restore_pg.sh`. Restore into a new database first whenever possible to avoid overwriting a running
instance.

```bash
# Example restore into staging
POSTGRES_HOST=staging.db.internal \
POSTGRES_DB=cleaning_staging \
POSTGRES_USER=app \
POSTGRES_PASSWORD=$(pass show staging/db/password) \
./scripts/restore_pg.sh /tmp/pg_cleaning_20250101T020000Z.dump cleaning_restore
```

Step-by-step:
1. Ensure the target database server is reachable and has enough disk space for the decompressed dump + indexes.
2. Copy the `.dump` file (and optional `.sha256`) to the restore host; verify checksum if available: `sha256sum -c pg_...dump.sha256`.
3. Run `scripts/restore_pg.sh <dump_file> [target_db]`. The script creates the target database if it does not exist and restores with `--clean`, `--if-exists`, `--no-owner`, and `--no-privileges`, so you can restore into a differently named database without conflicts. If the dump contains a `CREATE DATABASE` entry (from `pg_dump --create`), set `ALLOW_CREATE_IN_DUMP=1` to let `pg_restore` drop/create the database; otherwise the script will abort to avoid accidental clobbering.
4. Run Alembic migrations to head if needed: `alembic upgrade head` using the restored database URL.
5. Functional validation (staging/local):
   - `pytest -q` (or a fast smoke subset) against the restored DB URL.
   - Manual spot checks: booking creation, invoice view, webhook retries.
6. Cut over production only after staging succeeds; point application and jobs to the restored instance, then monitor errors/latency for 30–60 minutes.

## Restore drill checklist
Perform at least quarterly in staging:
- [ ] Trigger `scripts/backup_pg.sh` manually to generate a fresh dump.
- [ ] Restore the dump into a clean staging database via `scripts/restore_pg.sh`.
- [ ] Run Alembic migrations to head.
- [ ] Execute smoke tests: `pytest -q` (or selected suites), create a booking, view an invoice, and resend a webhook.
- [ ] Document duration from start to validation (compare to 4h RTO); note any blockers and update runbook/scripts accordingly.

## Uploads backup (order photos/PDFs)
Uploads live in object storage when `ORDER_STORAGE_BACKEND=s3` (default is local filesystem for development). Production environments should use
an S3-compatible bucket with **versioning enabled** so that uploads are protected automatically without relying on host-level rsync jobs.

Strategy:
- Enable bucket versioning and server-side encryption on the uploads bucket.
- Apply lifecycle rules: keep 30 days of non-current versions, expire delete markers after 30 days, and transition older versions to infrequent-access/Glacier tiers as appropriate.
- For environments still using `ORDER_STORAGE_BACKEND=local` (development only), run ad-hoc copies to a versioned bucket for recovery.

Restore uploads by copying the desired object version back into the bucket (or by promoting a previous version). After restore, invalidate any CDN cache that serves uploaded files.
