#!/usr/bin/env bash
set -euo pipefail

base_url=${1:-}
if [[ -z "${base_url}" ]]; then
  echo "Usage: $0 <base_url>" >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required" >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required" >&2
  exit 1
fi

log() { printf "[smoke] %s\n" "$*"; }
skip() { printf "[smoke][skip] %s\n" "$*"; }

auth_header() {
  local token="$1"
  printf "Authorization: Bearer %s" "$token"
}

log "Running production smoke against ${base_url}"

log "Checking /healthz"
curl -fsS "${base_url}/healthz" >/dev/null

log "Checking /readyz"
curl -fsS "${base_url}/readyz" >/dev/null

log "Requesting estimate"
estimate_payload='{
  "beds": 2,
  "baths": 1.5,
  "cleaning_type": "deep",
  "heavy_grease": true,
  "multi_floor": true,
  "frequency": "weekly",
  "add_ons": {
    "oven": true,
    "fridge": false,
    "microwave": true,
    "cabinets": false,
    "windows_up_to_5": true,
    "balcony": false,
    "linen_beds": 2,
    "steam_armchair": 0,
    "steam_sofa_2": 1,
    "steam_sofa_3": 0,
    "steam_sectional": 0,
    "steam_mattress": 0,
    "carpet_spot": 1
  }
}'

estimate_response=$(curl -fsS "${base_url}/v1/estimate" -H "Content-Type: application/json" -d "${estimate_payload}")
total_before_tax=$(printf '%s' "${estimate_response}" | jq -r '.total_before_tax')
log "Estimate succeeded (total_before_tax=${total_before_tax})"

lead_payload=$(python - <<'PY'
import json, os
estimate_request = json.loads(os.environ["ESTIMATE_REQUEST"])
estimate_response = json.loads(os.environ["ESTIMATE_RESPONSE"])
payload = {
    "name": "Smoke Test",
    "phone": "+15555550123",
    "email": "smoke@example.com",
    "postal_code": "T0T0T0",
    "preferred_dates": [],
    "structured_inputs": estimate_request,
    "estimate_snapshot": estimate_response,
}
print(json.dumps(payload))
PY
ESTIMATE_REQUEST="${estimate_payload}" ESTIMATE_RESPONSE="${estimate_response}")

captcha_mode=${CAPTCHA_MODE:-off}
if [[ "${captcha_mode}" != "off" ]]; then
  skip "Skipping lead creation because CAPTCHA_MODE=${captcha_mode}"
else
  log "Creating lead"
  lead_response=$(curl -fsS "${base_url}/v1/leads" -H "Content-Type: application/json" -d "${lead_payload}")
  lead_id=$(printf '%s' "${lead_response}" | jq -r '.lead_id')
  log "Lead created (lead_id=${lead_id})"
fi

if [[ -n "${STRIPE_SECRET_KEY:-}" ]]; then
  if [[ -z "${lead_id:-}" ]]; then
    skip "Stripe enabled but no lead created; skipping booking smoke"
  else
    log "Creating booking with deposit flow"
    starts_at=$(date -u -d "+1 hour" --iso-8601=seconds)
    booking_payload=$(python - <<'PY'
import json, os
print(json.dumps({
    "starts_at": os.environ["STARTS_AT"],
    "time_on_site_hours": 2,
    "lead_id": os.environ["LEAD_ID"],
    "service_type": "deep"
}))
PY
STARTS_AT="${starts_at}" LEAD_ID="${lead_id}")
    booking_response=$(curl -fsS "${base_url}/v1/bookings" -H "Content-Type: application/json" -d "${booking_payload}")
    deposit_required=$(printf '%s' "${booking_response}" | jq -r '.deposit_required')
    checkout_url=$(printf '%s' "${booking_response}" | jq -r '.checkout_url')
    log "Booking created (deposit_required=${deposit_required}, checkout_url_present=$( [[ -n "${checkout_url}" && "${checkout_url}" != "null" ]] && echo yes || echo no ))"
  fi
else
  skip "Stripe env vars not set; skipping booking/deposit smoke"
fi

if [[ -n "${ORDER_STORAGE_BACKEND:-}" ]]; then
  skip "Storage smoke not implemented in script; ensure admin auth and worker upload available"
else
  skip "Storage not configured; skipping photo flow"
fi

if [[ "${METRICS_ENABLED:-}" == "true" ]]; then
  token="${METRICS_TOKEN:-${METRICS_BEARER:-}}"
  if [[ -z "${token}" ]]; then
    skip "Metrics enabled but METRICS_TOKEN not provided; skipping metrics check"
  else
    log "Checking metrics endpoint gating"
    curl -fsS -H "Authorization: Bearer ${token}" "${base_url}/v1/metrics" >/dev/null
    log "Metrics endpoint accessible (token redacted)"
  fi
else
  skip "Metrics not enabled; skipping metrics endpoint"
fi

log "Smoke checks completed"
