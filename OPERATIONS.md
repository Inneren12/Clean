# Operations Guide

## Deploy order (Docker-first)
1. Provision Postgres (+ Redis if using external rate limiting).
2. Configure environment (`.env`) with DB URL, auth/portal secrets, Stripe/email keys, storage backend config, CORS/proxy settings.
3. Build and start API (`docker-compose up -d` or `make up`); ensure volumes for uploads (`order_upload_root`).
4. Run migrations inside the API container: `make migrate` (uses `alembic/` and `alembic.ini`).
5. Start scheduled jobs (cron/Scheduler) calling: `/v1/admin/cleanup`, `/v1/admin/email-scan`, `/v1/admin/retention/cleanup`, `/v1/admin/export-dead-letter`, `/v1/admin/outbox/dead-letter`, optional `storage_janitor` and `outbox-delivery` from `app/jobs/run.py`. Monitor loop health via `/v1/admin/jobs/status` (heartbeats, last success, consecutive failures).
6. Verify health endpoints and Stripe webhook secret; set `JOB_HEARTBEAT_REQUIRED=true` if monitoring job heartbeat.

## Lifecycle and services container
- FastAPI lifespan startup builds an `AppServices` bundle (storage, email adapter, Stripe client, rate limiter, metrics) on `app.state.services`; shutdown closes the rate limiter. Legacy aliases (`app.state.email_adapter`, `app.state.rate_limiter`, etc.) remain for compatibility during migration.
- Tenant resolution honors `X-Test-Org` only when running in testing mode or `APP_ENV=dev`; in prod the header is ignored.

## Postgres row-level security
- Migration `0044_postgres_rls_org_isolation` enables and forces RLS on org-owned tables (leads, bookings, invoices, invoice_payments, workers, teams, order_photos, export_events, email_events). The migration is a no-op on SQLite but must be applied in Postgres before rollout.
- The API sets a per-request `app.current_org_id` session variable via `SET LOCAL` at transaction start; use the application role (not a superuser) and ensure background jobs set org context before touching tenant tables.
- Verification: `SELECT * FROM pg_policies WHERE polname LIKE '%org_isolation%'` should list the policies; `SET LOCAL app.current_org_id = '<org_uuid>'; SELECT COUNT(*) FROM leads;` should only count rows for that org, and no rows are returned when the variable is unset.

## Environment variable groups
- **Auth & portals:** `AUTH_SECRET_KEY`, `CLIENT_PORTAL_SECRET`, `WORKER_PORTAL_SECRET`, Basic Auth username/password pairs, `LEGACY_BASIC_AUTH_ENABLED`. JWT/session TTLs come from `AUTH_ACCESS_TOKEN_TTL_MINUTES`, `AUTH_SESSION_TTL_MINUTES`, and `AUTH_REFRESH_TOKEN_TTL_MINUTES`.
- **Database:** `DATABASE_URL`, pool/timeout overrides; statement timeout controlled via `DATABASE_STATEMENT_TIMEOUT_MS`.
- **Rate limiting:** `RATE_LIMIT_PER_MINUTE`, `REDIS_URL`, proxy trust lists (`TRUST_PROXY_HEADERS`, `TRUSTED_PROXY_IPS`, `TRUSTED_PROXY_CIDRS`).
- **Admin safety:** `ADMIN_IP_ALLOWLIST_CIDRS` (optional CIDR list) gates `/v1/admin/*` and `/v1/iam/*` after resolving client IPs through trusted proxies; `ADMIN_READ_ONLY=true` converts POST/PUT/PATCH/DELETE on those routes into 409 Problem+JSON during incidents while allowing GETs for investigation. Owners/admins can mint org-scoped break-glass tokens with `/v1/admin/break-glass/start` (reason + TTL required) to permit temporary writes while the flag is on.
- **Stripe:** `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, success/cancel URLs, billing portal return URL, circuit breaker settings.
- **Email:** `EMAIL_MODE`, `SENDGRID_API_KEY` or `SMTP_*` values, retry/backoff settings, `EMAIL_FROM`/`EMAIL_FROM_NAME`. `EMAIL_TEMP_PASSWORDS=true` will deliver temp passwords in reset emails; leave false to send notification-only messages.
  Email adapters are resolved at runtime from `app.state.services.email_adapter` (with `app.state.email_adapter` as a backward-compatible alias) via `resolve_app_email_adapter`; admin email scans and scheduled jobs share this helper so tests can inject a stub adapter while production loads the configured SendGrid/SMTP adapter.
- **Storage/photos:** `ORDER_STORAGE_BACKEND`, `ORDER_UPLOAD_ROOT`, `ORDER_PHOTO_MAX_BYTES`, MIME allowlist, S3/R2/Cloudflare credentials, signing secrets/TTLs. Canonical storage keys follow `orders/{org_id}/{booking_id}/{photo_id}[.ext]` (legacy aliases still resolve for reads).
- **Feature flags:** `DEPOSITS_ENABLED`, `EXPORT_MODE` (`off`/`webhook`/`sheets`), and `STRICT_POLICY_MODE` for stricter portal/config behaviors. Operators can inspect runtime flags via `GET /v1/admin/feature-flags` (Basic Auth protected).
- **Captcha/abuse:** `CAPTCHA_MODE`, `TURNSTILE_SECRET_KEY`.
- **Metrics/observability:** `METRICS_ENABLED`, `METRICS_TOKEN`, `JOB_HEARTBEAT_REQUIRED`, `JOB_HEARTBEAT_TTL_SECONDS`.
- **Retention/export:** `RETENTION_*` settings, `EXPORT_MODE`, webhook URL/allowlist/backoff toggles.

## Health, readiness, and metrics
- `GET /healthz` – liveness.
- `GET /readyz` – checks DB connectivity, migration head vs `alembic/`, and job heartbeat when enabled (`app/api/routes_health.py`). Returns 503 on failure.
- Metrics middleware records HTTP latency/5xx counts (`app/main.py`, `app/infra/metrics.py`). When metrics are enabled, `/v1/metrics` router exposes admin-protected metrics export.
- Job metrics: `job_last_heartbeat_timestamp`, `job_last_success_timestamp`, and `job_errors_total` track scheduler health. View aggregated status via `/v1/admin/jobs/status`.

## Incident read-only + break-glass procedure
1. **Enable admin read-only:** set `ADMIN_READ_ONLY=true` (config/env and restart) to block POST/PUT/PATCH/DELETE on `/v1/admin/*` and `/v1/iam/*` with 409 Problem+JSON while investigations run. IP allowlists remain enforced.
2. **Start a break-glass session:** an OWNER/ADMIN calls `POST /v1/admin/break-glass/start` with `{"reason": "<incident summary>", "ttl_minutes": <minutes>}`. The API returns a one-time token and expiry; store it securely and never log it. The token is hashed at rest and scoped to the caller's org.
3. **Perform emergency writes:** include `X-Break-Glass-Token: <token>` on required admin write requests. Requests succeed only for the same org and until expiry. Every start and every write under break-glass is recorded in `admin_audit_logs` with the supplied reason.
4. **Disable:** remove `ADMIN_READ_ONLY` (set false + redeploy) once normal operations resume. Break-glass tokens naturally expire; discard any copies when read-only is lifted.

## Alerts and monitoring
- Prometheus alert examples in `ops/prometheus/alerts.yml` (readyz 5xx, error rate, P99 latency, job failures, DLQ backlog, Stripe circuit breaker). See `docs/runbook_monitoring.md` and `docs/runbook_incidents.md` for response steps.
- Track job heartbeat freshness and storage delete retry queues; alert on repeated Stripe/email circuit breaker opens (`app/infra/stripe_resilience.py`, `app/infra/email.py`). Use `/v1/admin/export-dead-letter` and `/v1/admin/export-dead-letter/{id}/replay` for DLQ backlog. Outbox failures are visible via `/v1/admin/outbox/dead-letter` with replay at `/v1/admin/outbox/{id}/replay`.

## Outbox delivery + DLQ
- Email/webhook/export sends enqueue `outbox_events` rows (unique per `org_id` + `dedupe_key`). Synchronous callers return 202 after enqueuing.
- `outbox-delivery` job (or `/v1/admin/outbox/dead-letter` replay) pops due rows, attempts delivery with exponential backoff (`outbox_base_backoff_seconds`, `outbox_max_attempts`).
- After `outbox_max_attempts`, rows move to status `dead` and exports also emit `export_events` for legacy DLQ visibility. Admins can replay dead rows without cross-org access.

## IAM onboarding operations
- Admin-issued onboarding and password resets are org-scoped via `/v1/iam/users/*`. Temp passwords are shown once in the API response and are only valid until the user changes their password; encourage operators to rotate sessions (`/v1/iam/users/{id}/logout`) if a credential leak is suspected.
- Ensure SaaS admin accounts can reach `/v1/iam/users` with correct org context; configure `AUTH_SECRET_KEY` and TTL env vars before enabling production onboarding.

## Backups and restores
- Postgres backups should capture tenant-scoped tables (`org_id` columns). Use `scripts/backup_pg.sh` (custom format, no `--create`) and `scripts/restore_pg.sh` (supports `ALLOW_CREATE_IN_DUMP=1` when the dump was made with `--create`). Validate restore before releases; ensure `alembic_version` matches after restore.

- Storage backends: verify bucket access and signed URL keys; for local storage, include `order_upload_root` volume in backups.

## Operator productivity queues (release-grade hardened)
- **Photos queue** (`GET /v1/admin/queue/photos`): requires dispatcher credentials or higher; lists photos awaiting review or retake. Filter by `status=pending|needs_retake|all`.
- **Invoices queue** (`GET /v1/admin/queue/invoices`): requires finance credentials or higher; lists overdue/unpaid invoices. Filter by `status=overdue|unpaid|all`.
- **Assignments queue** (`GET /v1/admin/queue/assignments`): requires dispatcher credentials or higher; lists unassigned bookings in next N days (default 7, max 30). Shows urgency indicator for bookings within 24h.
- **DLQ queue** (`GET /v1/admin/queue/dlq`): requires admin credentials only; lists failed outbox/export events. Filter by `kind=outbox|export|all`. Uses SQL-level pagination for scalability.
- **Timeline endpoints** (`GET /v1/admin/timeline/booking/{id}`, `/invoice/{id}`): requires viewer credentials or higher; PII is masked for viewer role. Shows unified audit logs, email events, payments, photo reviews, NPS, support tickets, and outbox events.
- **Role requirements**: dispatcher for photos/assignments, finance for invoices, admin for DLQ, viewer for timeline (with PII masking).
- **Performance notes**: DLQ uses SQL UNION ALL for combined queries; timeline limits each event type (100 audit logs, 100 emails, 50 payments, etc.) to prevent unbounded queries. Timeline queries avoid dangerous `LIKE %id%` patterns; outbox events use structured prefix patterns like `:booking:{id}` or `:invoice:{id}`.

## Config viewer and redaction
- `GET /v1/admin/config` surfaces a read-only snapshot of operational settings with secrets redacted (`<redacted>`). Only whitelisted keys are returned; secrets (tokens/keys/passwords) are never echoed.
- Keep config viewer behind admin Basic Auth and avoid piping responses into logs to prevent metadata leaks.

## Storage configuration
- **Local**: defaults to `ORDER_UPLOAD_ROOT=tmp` with files under `orders/{org_id}/{order_id}/...`; mount this path to durable storage or ensure it is backed up.
- **Cloudflare R2/S3-compatible**: set `ORDER_STORAGE_BACKEND=r2` (or `cloudflare_r2`) with `R2_BUCKET`, `R2_ACCESS_KEY`, `R2_SECRET_KEY`, optional `R2_ENDPOINT`/`R2_REGION`. Downloads use presigned GETs honoring `photo_url_ttl_seconds`.
- **Cloudflare Images**: set `ORDER_STORAGE_BACKEND=cloudflare_images` (or `cf_images`) with `CF_IMAGES_ACCOUNT_ID`, `CF_IMAGES_ACCOUNT_HASH`, `CF_IMAGES_API_TOKEN`, and `CF_IMAGES_SIGNING_KEY`; variants controlled by `CF_IMAGES_DEFAULT_VARIANT`/`CF_IMAGES_THUMBNAIL_VARIANT` and delivered via signed exp/sig redirects.
- **Delivery policy**: all backends require app-minted tokens (UA binding and one-time Redis optional) before issuing redirects; do not expose permanent public bucket URLs in admin/worker/client views.
