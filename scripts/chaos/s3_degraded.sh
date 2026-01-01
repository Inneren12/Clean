#!/usr/bin/env bash
set -euo pipefail

TARGET_HOST=${TARGET_HOST:-http://localhost:8000}
COMPOSE_BIN=${COMPOSE_BIN:-docker compose}
API_SERVICE=${API_SERVICE:-api}
CHAOS_S3_HOST=${CHAOS_S3_HOST:-s3.amazonaws.com}

info() { echo "[s3_degraded] $*"; }

cleanup() {
  ${COMPOSE_BIN} exec -T ${API_SERVICE} sh -c "ip route del blackhole ${CHAOS_S3_HOST}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

info "Blackholing S3 host ${CHAOS_S3_HOST} inside ${API_SERVICE}"
${COMPOSE_BIN} exec -T ${API_SERVICE} sh -c "ip route add blackhole ${CHAOS_S3_HOST} || true"

info "Probing S3 connectivity (expect timeout/failure)"
${COMPOSE_BIN} exec -T ${API_SERVICE} sh -c "python - <<'PY'
import socket, sys
host = '${CHAOS_S3_HOST}'
try:
    sock = socket.create_connection((host, 443), timeout=2)
    sock.close()
    print('S3 unexpectedly reachable')
    sys.exit(1)
except Exception as exc:  # noqa: BLE001
    print(f'S3 reachability blocked as expected: {type(exc).__name__}')
    sys.exit(0)
PY"

info "Issuing lightweight storage-less API request to confirm server responsiveness"
status=$(curl -s -o /tmp/s3_degraded_health -w "%{http_code}" --max-time 5 "${TARGET_HOST}/healthz" || echo "000")
cat /tmp/s3_degraded_health
if [[ "$status" == "000" ]]; then
  echo "API hang detected during S3 chaos"
  exit 1
fi

info "S3 degradation drill finished (routes will be cleaned up automatically)"
