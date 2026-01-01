# Production deployment guide

This guide walks through preparing secrets, provisioning infrastructure, and deploying the Cleaning Economy Bot to a production cluster using Docker images and `docker compose`. A new engineer should be able to ship with only this document and the sample `.env.production.example` file.

## Prerequisites

- Docker Engine 24+ and Docker Compose v2 installed on your workstation or build runner.
- Access to a production PostgreSQL database (with a non-root user) and the ability to run Alembic migrations.
- Access to a Redis instance for rate limiting and job heartbeats.
- DNS entries for the public API and any admin/portal frontends.
- A secrets manager (e.g., AWS SSM, Vault, Doppler) to store generated secrets.
- `openssl` or `python` available locally for secret generation.

## Generate required secrets

Use strong, random values for all secrets. Recommended commands:

```bash
# 32+ bytes hex for auth/portal/invoice secrets
openssl rand -hex 32

# 24+ bytes for metrics token
openssl rand -base64 32 | tr -d '=/' | cut -c1-48

# Stripe webhook secret is provided by Stripe; store it verbatim
```

Populate the generated values in a copy of `.env.production.example` and store the final `.env` in your secrets manager. Do **not** commit real secrets to git.

## Required environment variables

Copy `.env.production.example` to your deployment workspace and fill every placeholder. The file enumerates all runtime configuration the API expects. Critical groups to double-check:

- **Database:** `DATABASE_URL`, pooling fields, and `DATABASE_STATEMENT_TIMEOUT_MS`.
- **Redis:** `REDIS_URL` for rate limiting and job heartbeats.
- **Auth & portals:** `AUTH_SECRET_KEY`, `CLIENT_PORTAL_SECRET`, `WORKER_PORTAL_SECRET`.
- **Observability:** `METRICS_ENABLED=true` and `METRICS_TOKEN` (required in prod).
- **Storage:** `ORDER_STORAGE_BACKEND`, S3 credentials/region/endpoint, and `ORDER_PHOTO_SIGNING_SECRET`.
- **Exports:** `EXPORT_MODE`, `EXPORT_WEBHOOK_URL`, `EXPORT_WEBHOOK_ALLOWED_HOSTS`.
- **Stripe:** `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET`.

## Rollout order

1. **Migrations:** build/pull the production image, then run migrations against the production database:

   ```bash
   docker compose --env-file .env -f docker-compose.yml run --rm api alembic upgrade head
   ```

   Confirm the Alembic head matches using `/readyz` (see validation below).

2. **API service:** start the API after migrations succeed:

   ```bash
   docker compose --env-file .env -f docker-compose.yml up -d api
   ```

3. **Background jobs (if separated):** start the job runner container last so it sees the migrated schema and latest code:

   ```bash
   docker compose --env-file .env -f docker-compose.yml up -d jobs
   ```

## Validation and smoke tests

- **Liveness:** `curl https://api.example.com/healthz` should return `{ "status": "ok" }`.
- **Readiness:** `curl https://api.example.com/readyz` should report `status=ok`, `database.migrations_current=true`, and `jobs.ok=true`.
- **Metrics (authenticated):**

  ```bash
  curl -H "Authorization: Bearer $METRICS_TOKEN" https://api.example.com/metrics
  ```

  Expect Prometheus text output; a 401 indicates an invalid/missing token.
- **Stripe webhook signature:** verify `STRIPE_WEBHOOK_SECRET` matches the configured endpoint in Stripe Dashboard.
- **Exports disabled/enabled:** ensure `EXPORT_MODE` matches your launch plan (set to `off` if exports are not yet approved).

## Operational notes

- Enable proxy headers (`TRUST_PROXY_HEADERS=true`) when running behind a load balancer or ingress.
- Keep `METRICS_TOKEN` and auth secrets in your secret store; only inject via environment at runtime.
- If using Cloudflare Turnstile, set `CAPTCHA_MODE=turnstile` and populate `TURNSTILE_SECRET_KEY` before enabling in the UI.
