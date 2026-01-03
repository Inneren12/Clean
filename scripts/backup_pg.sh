#!/usr/bin/env bash
set -euo pipefail

# backup_pg.sh
# Creates a compressed pg_dump (custom format) for the configured database
# and prunes backups older than the configured retention window.

usage() {
  cat <<USAGE
Usage: POSTGRES_HOST=... POSTGRES_DB=... POSTGRES_USER=... POSTGRES_PASSWORD=... [OPTIONS] ./scripts/backup_pg.sh

Environment variables:
  POSTGRES_HOST        Hostname of the database server (required)
  POSTGRES_DB          Database name to back up (required)
  POSTGRES_USER        Database user with read permissions (required)
  POSTGRES_PASSWORD    Password for POSTGRES_USER (required)
  POSTGRES_PORT        Database port (default: 5432)
  BACKUP_DIR           Where to write dumps (default: ./backups)
  RETENTION_DAYS       Days to keep backups (default: 7)

Notes:
- The custom format includes schema + data and allows parallel restore.
- Run from a trusted host with network access to the database.
USAGE
}

for var in POSTGRES_HOST POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD; do
  if [[ -z "${!var:-}" ]]; then
    usage
    echo "Missing required env: $var" >&2
    exit 1
  fi
done

PORT="${POSTGRES_PORT:-5432}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DUMP_FILE="${BACKUP_DIR}/pg_${POSTGRES_DB}_${TIMESTAMP}.dump"

mkdir -p "$BACKUP_DIR"
export PGPASSWORD="$POSTGRES_PASSWORD"

echo "[backup] Writing dump to $DUMP_FILE"
pg_dump \
  --format=custom \
  --blobs \
  --no-owner \
  --no-privileges \
  --file="$DUMP_FILE" \
  --host="$POSTGRES_HOST" \
  --port="$PORT" \
  --username="$POSTGRES_USER" \
  "$POSTGRES_DB"

echo "[backup] Completed $(basename "$DUMP_FILE")"
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "$DUMP_FILE" >"${DUMP_FILE}.sha256"
  echo "[backup] SHA256 written to ${DUMP_FILE}.sha256"
fi

if [[ "$RETENTION_DAYS" =~ ^[0-9]+$ && "$RETENTION_DAYS" -gt 0 ]]; then
  echo "[backup] Pruning backups older than $RETENTION_DAYS days"
  find "$BACKUP_DIR" -type f -name "pg_${POSTGRES_DB}_*.dump" -mtime +"$((RETENTION_DAYS - 1))" -print -delete
fi

echo "[backup] Done"
