#!/usr/bin/env bash
set -euo pipefail

TARGET_HOST=${TARGET_HOST:-http://localhost:8000}
COMPOSE_BIN=${COMPOSE_BIN:-docker compose}
API_SERVICE=${API_SERVICE:-api}

info() { echo "[redis_down] $*"; }

info "Stopping redis service"
${COMPOSE_BIN} stop redis
sleep 2

info "Hitting health endpoint to ensure API responds without hanging"
status=$(curl -s -o /tmp/redis_down_health -w "%{http_code}" --max-time 5 "${TARGET_HOST}/healthz" || echo "000")
cat /tmp/redis_down_health
if [[ "$status" != "200" && "$status" != "503" ]]; then
  echo "Unexpected status during redis outage: $status"
  exit 1
fi

info "Posting a lightweight lead to confirm request handling"
payload='{"name":"Chaos Redis","phone":"780-555-9876","structured_inputs":{"beds":1,"baths":1,"cleaning_type":"standard","add_ons":{},"frequency":"one_time"},"estimate_snapshot":{"pricing_config_id":"economy","pricing_config_version":"v1","config_hash":"chaos","rate":25,"team_size":1,"time_on_site_hours":2,"billed_cleaner_hours":2,"labor_cost":50,"discount_amount":0,"add_ons_cost":0,"total_before_tax":100,"assumptions":[],"missing_info":[],"confidence":1}}'
lead_status=$(curl -s -o /tmp/redis_down_lead -w "%{http_code}" --max-time 5 -H "Content-Type: application/json" -d "$payload" "${TARGET_HOST}/v1/leads" || echo "000")
cat /tmp/redis_down_lead
if [[ "$lead_status" == "000" ]]; then
  echo "Lead request hung during redis outage"
  exit 1
fi

info "Restarting redis service"
${COMPOSE_BIN} start redis
${COMPOSE_BIN} exec -T redis redis-cli ping
info "Redis chaos drill complete"
