# Production Smoke Runbook

Use `scripts/smoke_prod.sh` to perform a minimal production smoke covering health/readiness, estimator, lead intake, and optional flows.

## Prerequisites
- Bash with `curl` and `jq` installed.
- Base URL reachable from your shell (e.g., `http://localhost:8000` or the production hostname).
- Environment variables for optional checks:
  - `CAPTCHA_MODE` — set to `off` to allow lead creation. When enabled, the script skips lead intake.
  - `STRIPE_SECRET_KEY` — when present, the script creates a booking linked to the smoke lead and validates the deposit checkout response.
  - `METRICS_ENABLED=true` and `METRICS_TOKEN` (or `METRICS_BEARER`) — validates `/v1/metrics` gating. Tokens are never printed.
  - `ORDER_STORAGE_BACKEND` — signals storage configuration. The script currently skips the storage smoke and reminds operators to verify photo upload/list/download manually with admin credentials.

## Commands
Run against any environment by passing the base URL:

```bash
bash scripts/smoke_prod.sh http://localhost:8000
```

### Expected behavior
- `/healthz` and `/readyz` failures stop the script with a non-zero exit code.
- Estimator response is logged with the total before tax.
- Lead creation runs when `CAPTCHA_MODE=off`; otherwise the script reports a skip.
- Stripe booking smoke runs when `STRIPE_SECRET_KEY` is set **and** a lead ID is available; otherwise it reports a skip.
- Metrics check runs only when metrics are enabled **and** a token is provided; skips otherwise.
- Storage flow is explicitly skipped unless implemented; use admin/worker credentials to validate uploads when storage is configured.

### Safety notes
- The script uses `set -euo pipefail` and never echoes secrets. Bearer tokens are only passed to `curl` headers and are redacted from logs.
- Keep environment variables set in your shell or an `.env` file; do not embed secrets in commands.
