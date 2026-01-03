# Operations Guide

## Deploy order (Docker-first)
1. Provision Postgres (+ Redis if using external rate limiting).
2. Configure environment (`.env`) with DB URL, auth/portal secrets, Stripe/email keys, storage backend config, CORS/proxy settings.
3. Build and start API (`docker-compose up -d` or `make up`); ensure volumes for uploads (`order_upload_root`).
4. Run migrations inside the API container: `make migrate` (uses `alembic/` and `alembic.ini`).
5. Start scheduled jobs (cron/Scheduler) calling: `/v1/admin/cleanup`, `/v1/admin/email-scan`, `/v1/admin/retention/cleanup`, `/v1/admin/export-dead-letter`, optional `storage_janitor` from `app/jobs/run.py`.
6. Verify health endpoints and Stripe webhook secret; set `JOB_HEARTBEAT_REQUIRED=true` if monitoring job heartbeat.

## Environment variable groups
- **Auth & portals:** `AUTH_SECRET_KEY`, `CLIENT_PORTAL_SECRET`, `WORKER_PORTAL_SECRET`, Basic Auth username/password pairs, `LEGACY_BASIC_AUTH_ENABLED`. JWT/session TTLs come from `AUTH_ACCESS_TOKEN_TTL_MINUTES`, `AUTH_SESSION_TTL_MINUTES`, and `AUTH_REFRESH_TOKEN_TTL_MINUTES`.
- **Database:** `DATABASE_URL`, pool/timeout overrides; statement timeout controlled via `DATABASE_STATEMENT_TIMEOUT_MS`.
- **Rate limiting:** `RATE_LIMIT_PER_MINUTE`, `REDIS_URL`, proxy trust lists (`TRUST_PROXY_HEADERS`, `TRUSTED_PROXY_IPS`, `TRUSTED_PROXY_CIDRS`).
- **Stripe:** `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, success/cancel URLs, billing portal return URL, circuit breaker settings.
- **Email:** `EMAIL_MODE`, `SENDGRID_API_KEY` or `SMTP_*` values, retry/backoff settings, `EMAIL_FROM`/`EMAIL_FROM_NAME`. `EMAIL_TEMP_PASSWORDS=true` will deliver temp passwords in reset emails; leave false to send notification-only messages.
- **Storage/photos:** `ORDER_STORAGE_BACKEND`, `ORDER_UPLOAD_ROOT`, `ORDER_PHOTO_MAX_BYTES`, MIME allowlist, S3/R2/Cloudflare credentials, signing secrets/TTLs.
- **Captcha/abuse:** `CAPTCHA_MODE`, `TURNSTILE_SECRET_KEY`.
- **Metrics/observability:** `METRICS_ENABLED`, `METRICS_TOKEN`, `JOB_HEARTBEAT_REQUIRED`, `JOB_HEARTBEAT_TTL_SECONDS`.
- **Retention/export:** `RETENTION_*` settings, `EXPORT_MODE`, webhook URL/allowlist/backoff toggles.

## Health, readiness, and metrics
- `GET /healthz` – liveness.
- `GET /readyz` – checks DB connectivity, migration head vs `alembic/`, and job heartbeat when enabled (`app/api/routes_health.py`). Returns 503 on failure.
- Metrics middleware records HTTP latency/5xx counts (`app/main.py`, `app/infra/metrics.py`). When metrics are enabled, `/v1/metrics` router exposes admin-protected metrics export.

## Alerts and monitoring
- Prometheus alert examples in `ops/prometheus/alerts.yml` (latency, error rate, readyz failures).
- Track job heartbeat freshness and storage delete retry queues; alert on repeated Stripe/email circuit breaker opens (`app/infra/stripe_resilience.py`, `app/infra/email.py`).

## IAM onboarding operations
- Admin-issued onboarding and password resets are org-scoped via `/v1/iam/users/*`. Temp passwords are shown once in the API response and are only valid until the user changes their password; encourage operators to rotate sessions (`/v1/iam/users/{id}/logout`) if a credential leak is suspected.
- Ensure SaaS admin accounts can reach `/v1/iam/users` with correct org context; configure `AUTH_SECRET_KEY` and TTL env vars before enabling production onboarding.

## Backups and restores
- Postgres backups should capture tenant-scoped tables (`org_id` columns). Validate restore before releases; ensure `alembic_version` matches after restore.
- Storage backends: verify bucket access and signed URL keys; for local storage, include `order_upload_root` volume in backups.
