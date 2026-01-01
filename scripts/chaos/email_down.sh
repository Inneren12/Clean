#!/usr/bin/env bash
set -euo pipefail

TARGET_HOST=${TARGET_HOST:-http://localhost:8000}
COMPOSE_BIN=${COMPOSE_BIN:-docker compose}
JOBS_SERVICE=${JOBS_SERVICE:-jobs}
EMAIL_HOST=${CHAOS_EMAIL_HOST:-smtp.sendgrid.net}
DISPATCH_BASIC=${DISPATCH_BASIC:-}

info() { echo "[email_down] $*"; }

cleanup() {
  ${COMPOSE_BIN} exec -T ${JOBS_SERVICE} sh -c "ip route del blackhole ${EMAIL_HOST}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

info "Blackholing email provider host ${EMAIL_HOST} inside ${JOBS_SERVICE}"
${COMPOSE_BIN} exec -T ${JOBS_SERVICE} sh -c "ip route add blackhole ${EMAIL_HOST} || true"

info "Triggering reminder scan to force email path"
auth_args=()
if [[ -n "$DISPATCH_BASIC" ]]; then
  auth_args=(-H "Authorization: Basic ${DISPATCH_BASIC}")
fi
scan_status=$(curl -s -o /tmp/email_down_scan -w "%{http_code}" --max-time 5 "${auth_args[@]}" -X POST "${TARGET_HOST}/v1/admin/email-scan" || echo "000")
cat /tmp/email_down_scan
if [[ "$scan_status" == "000" ]]; then
  echo "Email scan hung while provider was down"
  exit 1
fi

info "Probing SMTP connectivity inside jobs container (expect failure)"
${COMPOSE_BIN} exec -T ${JOBS_SERVICE} sh -c "python - <<'PY'
import socket, sys
host='${EMAIL_HOST}'
try:
    socket.create_connection((host, 587), timeout=2)
    print('SMTP unexpectedly reachable')
    sys.exit(1)
except Exception as exc:  # noqa: BLE001
    print(f'SMTP reachability blocked as expected: {type(exc).__name__}')
    sys.exit(0)
PY"

info "Email provider chaos drill complete (route cleanup handled by trap)"
