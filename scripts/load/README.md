# Load testing harness (k6)

This directory contains a k6-based harness that exercises the SaaS-critical flows:

- Lead intake → slot selection → booking creation.
- Optional invoice/payment webhook replay (when Stripe secrets are provided).
- Admin list endpoints (rate-limited flows).
- Storage upload/download round-trips when signed URLs are provided.
- Metrics scraping for DB pool utilization when an endpoint exports it.

## Quick start (local)

```bash
# Install k6 locally or run via Docker
export LOAD_TARGET_HOST=http://localhost:8000
export LOAD_ADMIN_BASIC=$(echo -n "admin:password" | base64 -w0)  # optional
export LOAD_DISPATCH_BASIC=$(echo -n "dispatcher:password" | base64 -w0)  # optional
export LOAD_VUS=5
export LOAD_DURATION=30s
k6 run scripts/load/saas_k6.js
```

Dockerized run (no local install required):

```bash
docker run --rm -it \
  -v "$PWD:/work" -w /work \
  -e LOAD_TARGET_HOST -e LOAD_ADMIN_BASIC -e LOAD_DISPATCH_BASIC \
  -e LOAD_BEARER_TOKEN -e LOAD_VUS -e LOAD_DURATION -e LOAD_SLEEP \
  -e LOAD_STORAGE_PUT_URL -e LOAD_STORAGE_GET_URL -e LOAD_STORAGE_BYTES \
  -e LOAD_METRICS_URL -e LOAD_STRIPE_SIGNATURE -e LOAD_STRIPE_EVENT_BODY \
  grafana/k6 run scripts/load/saas_k6.js
```

## Environment variables

- `LOAD_TARGET_HOST` (required): Base URL for the API (e.g., `https://api.example.com`).
- `LOAD_BEARER_TOKEN` (optional): Bearer token for authenticated flows.
- `LOAD_ADMIN_BASIC` / `LOAD_DISPATCH_BASIC` (optional): Base64-encoded `user:pass` for admin/dispatcher-only endpoints.
- `LOAD_VUS` (default `5`): Virtual user count.
- `LOAD_DURATION` (default `1m`): Test duration (k6 duration syntax).
- `LOAD_SLEEP` (default `1`): Think time between iterations (seconds).
- `LOAD_STORAGE_PUT_URL` / `LOAD_STORAGE_GET_URL`: Signed URLs used to measure storage upload/download latency and throughput.
- `LOAD_STORAGE_BYTES` (default `4096`): Payload size for storage uploads in bytes.
- `LOAD_METRICS_URL` (optional): Prometheus metrics endpoint to scrape DB pool usage (expects `db_pool_connections_in_use`).
- `LOAD_STRIPE_SIGNATURE` / `LOAD_STRIPE_EVENT_BODY` (optional): Inject a Stripe webhook payload to validate payment handling. The body may contain `{INVOICE_ID}` placeholder which is replaced with the booking ID.

A ready-to-tweak template lives in `.env.load.example`.

## Outputs and acceptance

- k6 prints per-group p95/p99 latency; thresholds fail the run when `http_req_failed` ≥ 5% or latency budgets are exceeded.
- Custom trends: `storage_upload_latency_ms`, `storage_download_latency_ms`, and `db_connections` (when exposed) help correlate infra saturation.
- Use `LOAD_METRICS_URL` against `/metrics` when `METRICS_ENABLED=true` to capture DB pool gauges exported by the runtime.

## CI smoke load (optional)

A short “smoke load” can run in CI by configuring `LOAD_TARGET_HOST` and credentials in secrets. The workflow is disabled unless a target host is provided, so it remains safe for forks while still exercising latency/error-rate budgets in main pipelines.

## DB pool and S3 notes

- DB pool utilization: when running against Postgres under Docker Compose, query `SELECT count(*) FROM pg_stat_activity WHERE datname='cleaning';` before/after the run to validate pool sizing. The `db_connections` trend will populate automatically if `/metrics` exports `db_pool_connections_in_use`.
- Storage throughput/latency: generate signed URLs from the target environment (or MinIO/localstack) and feed them into `LOAD_STORAGE_PUT_URL`/`LOAD_STORAGE_GET_URL`. The harness records upload/download timing separately.
