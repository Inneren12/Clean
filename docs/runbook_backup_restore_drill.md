# Backup/restore drill (local or staging)

This runbook walks through a full drill: take a backup from the current database, restore it into a fresh database, and verify schema + data health. Commands favor the provided helper scripts and avoid superuser-only operations.

## Prerequisites
- `pg_dump`, `pg_restore`, and `psql` installed on the host running the drill.
- Access credentials for the source database (read-only is fine for backups).
- A target database name that can be created for the restore (e.g., `cleaning_drill`).
- Repo root checked out so `./scripts/backup_pg.sh` and `./scripts/restore_pg.sh` are available.

## Step 1: Take a backup from the source database
Use the helper script to produce a custom-format dump. Customize the host/user/password values for your environment; do not log passwords.

```bash
cd /workspace/Clean  # repo root
export POSTGRES_HOST=localhost
export POSTGRES_DB=cleaning
export POSTGRES_USER=app
export POSTGRES_PASSWORD="<redacted>"
export BACKUP_DIR=./backups
export RETENTION_DAYS=7

./scripts/backup_pg.sh
# Dump is written to BACKUP_DIR (e.g., ./backups/pg_cleaning_YYYYMMDDTHHMMSSZ.dump)
```

## Step 2: Restore into a fresh database
Restore into a new database so production data stays untouched. The restore user only needs rights to create the target database and run `pg_restore --clean`.

```bash
# Optional: override if the restore host/user differ from the source
export RESTORE_POSTGRES_HOST=${RESTORE_POSTGRES_HOST:-$POSTGRES_HOST}
export RESTORE_POSTGRES_USER=${RESTORE_POSTGRES_USER:-$POSTGRES_USER}
export RESTORE_POSTGRES_PASSWORD=${RESTORE_POSTGRES_PASSWORD:-$POSTGRES_PASSWORD}
export RESTORE_POSTGRES_PORT=${RESTORE_POSTGRES_PORT:-${POSTGRES_PORT:-5432}}

RESTORE_DB=${RESTORE_DB:-cleaning_drill}
LATEST_DUMP=$(ls -1t ${BACKUP_DIR:-./backups}/pg_${POSTGRES_DB}_*.dump | head -n1)

POSTGRES_HOST="$RESTORE_POSTGRES_HOST" \
POSTGRES_PORT="$RESTORE_POSTGRES_PORT" \
POSTGRES_USER="$RESTORE_POSTGRES_USER" \
POSTGRES_PASSWORD="$RESTORE_POSTGRES_PASSWORD" \
POSTGRES_DB="$RESTORE_DB" \
  ./scripts/restore_pg.sh "$LATEST_DUMP" "$RESTORE_DB"
```

If the dump was created with `pg_dump --create`, rerun restore with `ALLOW_CREATE_IN_DUMP=1` to allow the script to drop/create the database safely.

## Step 3: Validate Alembic head and core data
Verify the restored database matches the current Alembic head and that org-scoped tables are present. Run these from the repo root (the Python snippet reads `alembic.ini`).

```bash
cd /workspace/Clean
# Get the expected Alembic head revision from the repo
ALEMBIC_HEAD=$(python - <<'PY'
from alembic.config import Config
from alembic.script import ScriptDirectory
cfg = Config("alembic.ini")
heads = ScriptDirectory.from_config(cfg).get_heads()
print(heads[0] if heads else "")
PY
)

# Check the restored database version
export PGPASSWORD="$RESTORE_POSTGRES_PASSWORD"
RESTORE_DB=${RESTORE_DB:-cleaning_drill}
RESTORE_PORT=${RESTORE_POSTGRES_PORT:-${POSTGRES_PORT:-5432}}
RESTORE_HOST=${RESTORE_POSTGRES_HOST:-$POSTGRES_HOST}
RESTORE_USER=${RESTORE_POSTGRES_USER:-$POSTGRES_USER}

DB_VERSION=$(psql \
  --host="$RESTORE_HOST" \
  --port="$RESTORE_PORT" \
  --username="$RESTORE_USER" \
  --dbname="$RESTORE_DB" \
  --tuples-only --no-align \
  --command "SELECT version_num FROM alembic_version LIMIT 1;")

if [[ "$DB_VERSION" != "$ALEMBIC_HEAD" ]]; then
  echo "[drill] Alembic mismatch: expected $ALEMBIC_HEAD, got $DB_VERSION" >&2
  exit 1
fi

# Sanity-check org-scoped tables exist and have rows
psql --host="$RESTORE_HOST" --port="$RESTORE_PORT" --username="$RESTORE_USER" --dbname="$RESTORE_DB" <<'SQL'
SELECT to_regclass('public.orgs') AS orgs_table,
       to_regclass('public.leads') AS leads_table,
       to_regclass('public.bookings') AS bookings_table,
       to_regclass('public.invoices') AS invoices_table;
SELECT COUNT(*) AS org_count FROM orgs;
SELECT COUNT(*) AS leads_any FROM leads LIMIT 1;
SQL
```

A passing drill has:
- `DB_VERSION` equal to the `ALEMBIC_HEAD` from the repo.
- All org-scoped tables present (non-null `to_regclass` results) and at least one org row if your environment has seeded data.

## Optional: Automate with scripts/backup_restore_drill.sh
Use `./scripts/backup_restore_drill.sh` for a single command that runs the backup, restore, and Alembic/table checks without printing secrets. See the script header for required environment variables. It exits non-zero if validation fails.

## Cleanup
When finished, drop the drill database if you do not need it:

```bash
dropdb --host="$RESTORE_HOST" --port="$RESTORE_PORT" --username="$RESTORE_USER" "$RESTORE_DB"
```
