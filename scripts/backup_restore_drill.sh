#!/usr/bin/env bash
set -euo pipefail

# backup_restore_drill.sh
# Runs a full backup + restore drill using the helper scripts and validates
# that the restored database is on the current Alembic head with core tables present.

usage() {
  cat <<'USAGE'
Usage: POSTGRES_HOST=... POSTGRES_DB=... POSTGRES_USER=... POSTGRES_PASSWORD=... ./scripts/backup_restore_drill.sh

Environment variables (source database for backup):
  POSTGRES_HOST             Hostname of the source database (required)
  POSTGRES_DB               Source database name (required)
  POSTGRES_USER             User with read access to the source DB (required)
  POSTGRES_PASSWORD         Password for POSTGRES_USER (required)
  POSTGRES_PORT             Source database port (default: 5432)
  BACKUP_DIR                Directory to store the dump (default: ./backups)
  RETENTION_DAYS            Retention window passed to backup_pg.sh (default: 7)

Restore target (defaults to source host/user):
  RESTORE_POSTGRES_HOST     Hostname for restore (default: POSTGRES_HOST)
  RESTORE_POSTGRES_USER     User for restore (default: POSTGRES_USER)
  RESTORE_POSTGRES_PASSWORD Password for restore user (default: POSTGRES_PASSWORD)
  RESTORE_POSTGRES_PORT     Port for restore (default: POSTGRES_PORT or 5432)
  RESTORE_TARGET_DB         Database name to restore into (default: <POSTGRES_DB>_drill)
  ALLOW_CREATE_IN_DUMP      Set to 1 to allow restore when dump contains CREATE DATABASE

Notes:
- The script does not print passwords. Supply credentials via environment or a sourced file.
- The restore user only needs permission to create the target database and run pg_restore --clean.
USAGE
}

for var in POSTGRES_HOST POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD; do
  if [[ -z "${!var:-}" ]]; then
    usage
    echo "Missing required env: $var" >&2
    exit 1
  fi
done

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-${ROOT_DIR}/backups}"
PORT="${POSTGRES_PORT:-5432}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DUMP_FILE="${BACKUP_DIR}/pg_${POSTGRES_DB}_${TIMESTAMP}.dump"

RESTORE_HOST="${RESTORE_POSTGRES_HOST:-$POSTGRES_HOST}"
RESTORE_USER="${RESTORE_POSTGRES_USER:-$POSTGRES_USER}"
RESTORE_PASSWORD="${RESTORE_POSTGRES_PASSWORD:-$POSTGRES_PASSWORD}"
RESTORE_PORT="${RESTORE_POSTGRES_PORT:-$PORT}"
RESTORE_DB="${RESTORE_TARGET_DB:-${POSTGRES_DB}_drill}"

mkdir -p "$BACKUP_DIR"

echo "[drill] Backing up from ${POSTGRES_USER}@${POSTGRES_HOST}:${PORT}/${POSTGRES_DB}"
POSTGRES_PORT="$PORT" \
BACKUP_DIR="$BACKUP_DIR" \
RETENTION_DAYS="${RETENTION_DAYS:-7}" \
  ./scripts/backup_pg.sh

if [[ ! -f "$DUMP_FILE" ]]; then
  echo "[drill] Expected dump not found at $DUMP_FILE" >&2
  echo "Check BACKUP_DIR and naming; ensure backup_pg.sh completed successfully." >&2
  exit 1
fi

echo "[drill] Restoring into ${RESTORE_USER}@${RESTORE_HOST}:${RESTORE_PORT}/${RESTORE_DB}"
POSTGRES_HOST="$RESTORE_HOST" \
POSTGRES_PORT="$RESTORE_PORT" \
POSTGRES_USER="$RESTORE_USER" \
POSTGRES_PASSWORD="$RESTORE_PASSWORD" \
POSTGRES_DB="$RESTORE_DB" \
ALLOW_CREATE_IN_DUMP="${ALLOW_CREATE_IN_DUMP:-0}" \
  ./scripts/restore_pg.sh "$DUMP_FILE" "$RESTORE_DB"

cd "$ROOT_DIR"
ALEMBIC_HEAD=$(python - <<'PY'
from alembic.config import Config
from alembic.script import ScriptDirectory
cfg = Config("alembic.ini")
heads = ScriptDirectory.from_config(cfg).get_heads()
print(heads[0] if heads else "")
PY
)

if [[ -z "$ALEMBIC_HEAD" ]]; then
  echo "[drill] Unable to determine Alembic head" >&2
  exit 1
fi

DB_VERSION=$(PGPASSWORD="$RESTORE_PASSWORD" psql \
  --host="$RESTORE_HOST" \
  --port="$RESTORE_PORT" \
  --username="$RESTORE_USER" \
  --dbname="$RESTORE_DB" \
  --tuples-only --no-align \
  --command "SELECT version_num FROM alembic_version LIMIT 1;" || true)

if [[ -z "$DB_VERSION" ]]; then
  echo "[drill] Could not read alembic_version from restored database" >&2
  exit 1
fi

if [[ "$DB_VERSION" != "$ALEMBIC_HEAD" ]]; then
  echo "[drill] Alembic mismatch: expected $ALEMBIC_HEAD, got $DB_VERSION" >&2
  exit 1
fi

declare -a missing_tables=()
for table in orgs leads bookings invoices workers; do
  PRESENT=$(PGPASSWORD="$RESTORE_PASSWORD" psql \
    --host="$RESTORE_HOST" \
    --port="$RESTORE_PORT" \
    --username="$RESTORE_USER" \
    --dbname="$RESTORE_DB" \
    --tuples-only --no-align \
    --command "SELECT to_regclass('public.${table}') IS NOT NULL;" || true)
  if [[ "$PRESENT" != "t" ]]; then
    missing_tables+=("$table")
  fi
done

if [[ ${#missing_tables[@]} -gt 0 ]]; then
  echo "[drill] Missing expected tables: ${missing_tables[*]}" >&2
  exit 1
fi

echo "[drill] Backup/restore drill succeeded. Alembic head $ALEMBIC_HEAD confirmed on ${RESTORE_DB}."
