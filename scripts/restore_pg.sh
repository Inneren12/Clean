#!/usr/bin/env bash
set -euo pipefail

# restore_pg.sh
# Restores a pg_dump (custom format) into the specified database.

usage() {
  cat <<USAGE
Usage: POSTGRES_HOST=... POSTGRES_DB=... POSTGRES_USER=... POSTGRES_PASSWORD=... ./scripts/restore_pg.sh <dump_file> [target_db]

Arguments:
  dump_file     Path to the .dump file to restore (required)
  target_db     Target database name (default: POSTGRES_DB)

Environment variables:
  POSTGRES_HOST        Hostname of the database server (required)
  POSTGRES_DB          Default target database name
  POSTGRES_USER        Database user with restore permissions (required)
  POSTGRES_PASSWORD    Password for POSTGRES_USER (required)
  POSTGRES_PORT        Database port (default: 5432)

Notes:
- The script will create the target database if it does not exist.
- The restore runs with --clean and --if-exists to replace objects safely.
USAGE
}

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

for var in POSTGRES_HOST POSTGRES_USER POSTGRES_PASSWORD; do
  if [[ -z "${!var:-}" ]]; then
    usage
    echo "Missing required env: $var" >&2
    exit 1
  fi
done

DUMP_FILE="$1"
if [[ ! -f "$DUMP_FILE" ]]; then
  echo "Dump file not found: $DUMP_FILE" >&2
  exit 1
fi

TARGET_DB="${2:-${POSTGRES_DB:-}}"
if [[ -z "$TARGET_DB" ]]; then
  usage
  echo "Target database must be provided (argument or POSTGRES_DB)" >&2
  exit 1
fi

PORT="${POSTGRES_PORT:-5432}"
export PGPASSWORD="$POSTGRES_PASSWORD"

DATABASE_DIRECTIVE=$(pg_restore --list "$DUMP_FILE" | grep -E "^;\s*Database" || true)
USE_CREATE=${ALLOW_CREATE_IN_DUMP:-0}
if [[ -n "$DATABASE_DIRECTIVE" && "$USE_CREATE" != "1" ]]; then
  echo "[restore] Dump contains CREATE DATABASE; rerun backup without --create or set ALLOW_CREATE_IN_DUMP=1" >&2
  exit 1
fi

CONNECT_DB="$TARGET_DB"
RESTORE_ARGS=(
  --verbose
  --clean
  --if-exists
  --no-owner
  --no-privileges
  --exit-on-error
)
if [[ -n "$DATABASE_DIRECTIVE" && "$USE_CREATE" == "1" ]]; then
  CONNECT_DB="postgres"
  RESTORE_ARGS+=(--create)
fi

# Create database if missing
DB_EXISTS=$(psql \
  --host="$POSTGRES_HOST" \
  --port="$PORT" \
  --username="$POSTGRES_USER" \
  --dbname=postgres \
  --tuples-only \
  --no-align \
  --command "SELECT 1 FROM pg_database WHERE datname='${TARGET_DB}'" || true)

if [[ "$DB_EXISTS" != "1" && "$CONNECT_DB" == "$TARGET_DB" ]]; then
  echo "[restore] Creating database $TARGET_DB"
  createdb \
    --host="$POSTGRES_HOST" \
    --port="$PORT" \
    --username="$POSTGRES_USER" \
    "$TARGET_DB"
fi

echo "[restore] Restoring $DUMP_FILE into $TARGET_DB"
pg_restore \
  "${RESTORE_ARGS[@]}" \
  --dbname="$CONNECT_DB" \
  --host="$POSTGRES_HOST" \
  --port="$PORT" \
  --username="$POSTGRES_USER" \
  "$DUMP_FILE"

echo "[restore] Completed restore into $TARGET_DB"
