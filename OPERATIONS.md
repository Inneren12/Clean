# Operations Guide

## Deploy order (Docker-first)
1. Provision Postgres (+ Redis if using external rate limiting).
2. Configure environment (`.env`) with DB URL, auth/portal secrets, Stripe/email keys, storage backend config, CORS/proxy settings.
3. Build and start API (`docker-compose up -d` or `make up`); ensure volumes for uploads (`order_upload_root`).
4. Run migrations inside the API container: `make migrate` (uses `alembic/` and `alembic.ini`).
5. Start scheduled jobs (cron/Scheduler) calling: `/v1/admin/cleanup`, `/v1/admin/email-scan`, `/v1/admin/retention/cleanup`, `/v1/admin/export-dead-letter`, `/v1/admin/outbox/dead-letter`, optional `storage_janitor` and `outbox-delivery` from `app/jobs/run.py`. Monitor loop health via `/v1/admin/jobs/status` (heartbeats, last success, consecutive failures).
6. Verify health endpoints and Stripe webhook secret; set `JOB_HEARTBEAT_REQUIRED=true` if monitoring job heartbeat.

## Scheduler templates
- Cron and Cloudflare Scheduler examples live in `scripts/cron_examples/` with a runbook in `docs/runbook_scheduler.md`.

## Scheduling v2 endpoints & usage
- **Suggestions:** `GET /v1/admin/schedule/suggestions` (dispatcher credentials required) returns available teams and workers for a start/end window. Optional `skill_tags` filter workers by matching role text and `booking_id` excludes the in-flight booking from overlap calculations. Responses are org-scoped and omit workers whose teams are blocked by bookings/blackouts.
- **Conflict checks:** `GET /v1/admin/schedule/conflicts` (dispatcher credentials required) surfaces blocking bookings, blackouts, or worker conflicts for the provided window. Supports optional `team_id`, `booking_id` (to ignore the current booking), and `worker_id` to validate an assignment. Returns Problem+JSON 403 for cross-org access attempts and 422 for invalid windows.

## Accounting export v1
- Finance-only CSV export is available at `/v1/admin/exports/accounting.csv` with optional `from`, `to`, and repeated `status` query parameters. Totals come from stored invoice snapshots (taxable_subtotal_cents, tax_cents, total_cents) plus succeeded payments and balances; CSV cells are injection-hardened.
- Schedule monthly exports by adding `--job accounting-export` to the jobs runner (defaults to the previous calendar month). When `EXPORT_MODE=webhook` and `EXPORT_WEBHOOK_URL` is set the job enqueues an outbox export webhook; otherwise the CSV artifact is stored in `export_events` with `mode=accounting_csv` for review.

## Pause/resume playbook
- SaaS OWNER/ADMIN/FINANCE roles can pause billing via `POST /v1/billing/pause` with a reason code (64 chars max). The pause sets the subscription status to `paused`, records `pause_reason_code`, and timestamps `paused_at` for auditability.
- Resume with `POST /v1/billing/resume` (same roles) to return status to `active`, capture `resume_reason_code`, and stamp `resumed_at`. Plan metadata is preserved; only the status gates entitlements.
- Both endpoints are org-scoped and require the caller’s org context; use `/v1/billing/status` to verify status/reason/timestamps after each action.

## Dunning-lite rules
- Stripe invoice payment failures enqueue a single outbox email per invoice (`dedupe_key=invoice:{invoice_id}:dunning:payment_failed`) using lead contact metadata—no raw Stripe payloads are stored.
- Email copy is minimal: invoice number, amount, and a friendly retry prompt; the outbox backoff controls resend timing and prevents spam.
- Operators can monitor/replay via standard outbox/DLQ tooling; retries follow global `outbox_base_backoff_seconds`/`outbox_max_attempts` settings.

## Lifecycle and services container
- FastAPI lifespan startup builds an `AppServices` bundle (storage, email adapter, Stripe client, rate limiter, metrics) on `app.state.services`; shutdown closes the rate limiter. Legacy aliases (`app.state.email_adapter`, `app.state.rate_limiter`, etc.) remain for compatibility during migration.
- Tenant resolution honors `X-Test-Org` only when running in testing mode or `APP_ENV=dev`; in prod the header is ignored.
- Production config validation fails fast when `APP_ENV=prod`: default/placeholder auth and portal secrets are rejected, `STRICT_CORS=true` requires explicit CORS origins (no `*`), metrics require `METRICS_TOKEN`, admin IP CIDRs must parse, and testing overrides (including `X-Test-Org`) stay disabled.

## Postgres row-level security
- Migration `0044_postgres_rls_org_isolation` enables and forces RLS on org-owned tables (leads, bookings, invoices, invoice_payments, workers, teams, order_photos, export_events, email_events). The migration is a no-op on SQLite but must be applied in Postgres before rollout.
- The API sets a per-request `app.current_org_id` session variable via `SET LOCAL` at transaction start; use the application role (not a superuser) and ensure background jobs set org context before touching tenant tables.
- Verification: `SELECT * FROM pg_policies WHERE polname LIKE '%org_isolation%'` should list the policies; `SET LOCAL app.current_org_id = '<org_uuid>'; SELECT COUNT(*) FROM leads;` should only count rows for that org, and no rows are returned when the variable is unset.

## Environment variable groups
- **Auth & portals:** `AUTH_SECRET_KEY`, `CLIENT_PORTAL_SECRET`, `WORKER_PORTAL_SECRET`, Basic Auth username/password pairs, `LEGACY_BASIC_AUTH_ENABLED`. JWT/session TTLs come from `AUTH_ACCESS_TOKEN_TTL_MINUTES`, `AUTH_SESSION_TTL_MINUTES`, and `AUTH_REFRESH_TOKEN_TTL_MINUTES`.
- **Legacy Basic Auth hardening:** In production, `LEGACY_BASIC_AUTH_ENABLED` now defaults to `false` to reduce attack surface; set it to `true` only if SaaS auth is unavailable. Prefer SaaS identities with `ADMIN_MFA_REQUIRED=true` for admin/finance access.
- **Database:** `DATABASE_URL`, pool/timeout overrides; statement timeout controlled via `DATABASE_STATEMENT_TIMEOUT_MS`.
- **Rate limiting:** `RATE_LIMIT_PER_MINUTE`, `REDIS_URL`, proxy trust lists (`TRUST_PROXY_HEADERS`, `TRUSTED_PROXY_IPS`, `TRUSTED_PROXY_CIDRS`).
- **Admin safety:** `ADMIN_IP_ALLOWLIST_CIDRS` (optional CIDR list) gates `/v1/admin/*` and `/v1/iam/*` after resolving client IPs through trusted proxies; `ADMIN_READ_ONLY=true` converts POST/PUT/PATCH/DELETE on those routes into 409 Problem+JSON during incidents while allowing GETs for investigation. Owners/admins can mint org-scoped break-glass tokens with `/v1/admin/break-glass/start` (reason + TTL required) to permit temporary writes while the flag is on.
- **Admin MFA:** `ADMIN_MFA_REQUIRED=true` enables RFC6238 TOTP for OWNER/ADMIN SaaS users. Enrollment (`/v1/auth/2fa/enroll`) returns a base32 secret + `otpauth://` URI; verification (`/v1/auth/2fa/verify`) enables TOTP and revokes sessions. Login/refresh for those roles require a 6-digit code when TOTP is enabled; missing/invalid codes return 401 Problem+JSON with `type=mfa_required`. `/v1/admin/*` and `/v1/iam/*` reject SaaS tokens lacking `mfa_verified` while the flag is on. `/v1/auth/2fa/disable` (OWNER-only) clears the secret and revokes sessions if a device is lost. Legacy Basic Auth is not MFA-capable.
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

## How to detect invoice mismatches
- Finance-only read endpoint: `GET /v1/admin/finance/reconcile/invoices?status=mismatch` (or `status=all` to include clean invoices) returns invoices where payments and invoice status disagree. Basic Auth finance roles or SaaS FINANCE tokens are required.
- Org isolation: requests are scoped by the resolved org (`X-Test-Org` in dev/tests) and will not surface invoices from other tenants.
- Response fields include payment counts, last payment timestamp, and quick action placeholders pointing at `/v1/admin/finance/invoices/{invoice_id}/reconcile` for remediation.

## Invoice tax snapshots and reporting
- Invoices store tax snapshots at creation time: `taxable_subtotal_cents` (sum of line totals with a positive tax rate), `tax_cents`, and `tax_rate_basis` (effective rate derived from the stored amounts). These values are persisted alongside `subtotal_cents` and are not recomputed from current org tax configs.
- Backfill never treats zero-tax invoices as taxable; only invoices with `tax_cents > 0` infer `taxable_subtotal_cents` from the subtotal during historical migrations.
- GST and P&L reports read the stored invoice snapshots, so changing org tax settings or estimate snapshots after issuing an invoice will not mutate historical tax totals. When editing invoice items, call `recalculate_totals` to refresh the stored snapshot fields from the invoice items.
- Invoice creation computes and persists the snapshot fields at issuance time; later org config changes (for example, updating a GST rate in pricing or lead snapshots) must **not** trigger recomputation. Treat `tax_cents`/`taxable_subtotal_cents`/`tax_rate_basis` as immutable snapshots for reporting and exports.
### How to reconcile an invoice safely
- Use finance credentials and call `POST /v1/admin/finance/invoices/{invoice_id}/reconcile` in the correct org context. The action locks the invoice row, recomputes succeeded manual/Stripe payments, and updates invoice status accordingly (PAID when succeeded funds cover total, PARTIAL when some funds exist, or SENT/OVERDUE when a PAID invoice has no settled funds and the due date has passed).
- The reconcile action is idempotent: repeat calls do not create duplicate payments or alter Stripe-settled amounts. If no succeeded payments exist, the service will **not** invent Stripe records; it simply reopens the invoice.
- Every reconcile call records an admin audit log with before/after snapshots (status, paid cents, outstanding cents, succeeded payment count). Review the unified timeline if you need to verify who performed a repair.

## Billing and usage reporting
- SaaS operators with finance/admin/owner roles can fetch per-org usage and plan limits via `GET /v1/billing/usage/report` using SaaS JWTs. Viewer/dispatcher roles are rejected.
- The response includes current workers, bookings in the selected month (default current month), and total storage bytes alongside plan limits, overage flags, and recorded usage counters.
- Drift detection compares stored usage events against the computed truth from workers/bookings/photos; discrepancies are surfaced per metric and flagged via `drift_detected`.
- Optional `month=YYYY-MM` query parameter pins the booking window to a specific month; storage and worker counts remain current-state snapshots.
## Stripe events view (read-only)
- Finance-only endpoint: `GET /v1/admin/finance/reconcile/stripe-events` lists recent Stripe webhook events for the caller's org. Requires FINANCE credentials (admin/accountant/owner) or SaaS FINANCE tokens.
- Filters: `invoice_id`, `booking_id`, `status` plus `limit`/`offset` pagination. Results are ordered by event creation/processing time, newest first.
- Response fields: `event_id`, `type`, `created_at`, `org_id`, optional `invoice_id`/`booking_id`, `processed_status`, and `last_error`. Raw webhook payloads are **not** returned; only metadata captured during processing is surfaced.

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

## Data export/delete playbook
- OWNER/ADMIN roles only; all calls are org-scoped (`X-Test-Org` in dev/tests). Every export/deletion request is captured in admin audit logs with actor and resource metadata.
- **Data export**: `POST /v1/admin/data/export` with `lead_id` or `email` returns a JSON bundle of leads and linked bookings, invoices, payments, and photo references. Only metadata is returned—no signed URLs or tokens.
- **Deletion requests**: `POST /v1/admin/data-deletion/requests` records a request and marks matching leads as pending deletion. It is safe to enqueue multiple requests; they stay scoped to the caller’s org.
- **Retention cleanup**: `POST /v1/admin/retention/cleanup` also processes pending deletion requests. Cleanup deletes photos (tombstoned for storage retry), detaches bookings from leads, removes invoice public tokens, nulls invoice `customer_id`, and anonymizes lead PII while retaining invoice totals/tax data for accounting.

## IAM onboarding operations
- Admin-issued onboarding and password resets are org-scoped via `/v1/iam/users/*`. Temp passwords are shown once in the API response and are only valid until the user changes their password; encourage operators to rotate sessions (`/v1/iam/users/{id}/logout`) if a credential leak is suspected.
- Ensure SaaS admin accounts can reach `/v1/iam/users` with correct org context; configure `AUTH_SECRET_KEY` and TTL env vars before enabling production onboarding.

## Client portal v1 usage
- Magic links are issued from `/client/login/request` and verified via `/client/login/callback`. Links are HMAC-signed with `CLIENT_PORTAL_SECRET`, embed the `org_id`, and respect `CLIENT_PORTAL_TOKEN_TTL_MINUTES`; tokens must be sent as `client_session` cookies or `Authorization: Bearer` headers.
- Client requests are org-scoped from the token: `/v1/client/portal/bookings*` (upcoming list + detail), `/v1/client/portal/invoices*`, and photo signed URLs require the same org. Cross-org identifiers return 404/403 even when the booking ID is guessed.
- Photo access uses signed download redirects only (`/v1/orders/{order_id}/photos/{photo_id}/signed-download`); do not serve raw bucket URLs. Ensure storage signing keys and `PHOTO_URL_TTL_SECONDS` are configured and rotate tokens if a leak is suspected.
- Rate limits apply to client portal endpoints using the shared rate limiter; keep Redis available in production to avoid abuse and monitor `429` spikes for scraping attempts.

## Backups and restores
- Postgres backups should capture tenant-scoped tables (`org_id` columns). Use `scripts/backup_pg.sh` (custom format, no `--create`) and `scripts/restore_pg.sh` (supports `ALLOW_CREATE_IN_DUMP=1` when the dump was made with `--create`). Validate restore before releases; ensure `alembic_version` matches after restore.
- Run the quarterly/local drill described in `docs/runbook_backup_restore_drill.md` to practice backup → restore → verification on a fresh database.

- Storage backends: verify bucket access and signed URL keys; for local storage, include `order_upload_root` volume in backups.

## Operator productivity queues (release-grade hardened)
- **Photos queue** (`GET /v1/admin/queue/photos`): requires dispatcher credentials or higher; lists photos awaiting review or retake. Filter by `status=pending|needs_retake|all`.
- **Invoices queue** (`GET /v1/admin/queue/invoices`): requires finance credentials or higher; lists overdue/unpaid invoices. Filter by `status=overdue|unpaid|all`.
- **Assignments queue** (`GET /v1/admin/queue/assignments`): requires dispatcher credentials or higher; lists unassigned bookings in next N days (default 7, max 30). Shows urgency indicator for bookings within 24h.
- **DLQ queue** (`GET /v1/admin/queue/dlq`): requires admin credentials only; lists failed outbox/export events. Filter by `kind=outbox|export|all`. Uses SQL-level pagination for scalability.
- **Timeline endpoints** (`GET /v1/admin/timeline/booking/{id}`, `/invoice/{id}`): requires viewer credentials or higher; PII is masked for viewer role. Shows unified audit logs, email events, payments, photo reviews, NPS, support tickets, and outbox events.
- **Role requirements**: dispatcher for photos/assignments, finance for invoices, admin for DLQ, viewer for timeline (with PII masking).
- **Performance notes**: DLQ uses SQL UNION ALL for combined queries; timeline limits each event type (100 audit logs, 100 emails, 50 payments, etc.) to prevent unbounded queries. Timeline queries avoid dangerous `LIKE %id%` patterns; outbox events use structured prefix patterns like `:booking:{id}` or `:invoice:{id}`.

## Analytics v1 endpoints
- `GET /v1/admin/analytics/funnel`: finance/admin/owner roles only. Returns aggregate counts for leads → bookings → completed jobs → paid payments plus conversion rates. All queries are org-scoped by `org_id`.
- `GET /v1/admin/analytics/nps`: finance/admin/owner roles only. Returns NPS distribution (promoters/passives/detractors) and weekly/monthly average score trends. Results are aggregates only—no comments or client identifiers.
- `GET /v1/admin/analytics/cohorts`: finance/admin/owner roles only. Groups customers by their first booking month and reports repeat counts and rates. Cohorts are computed per-organization; no raw PII is returned.

## Config viewer and redaction
- `GET /v1/admin/config` surfaces a read-only snapshot of operational settings with secrets redacted (`<redacted>`). Only whitelisted keys are returned; secrets (tokens/keys/passwords) are never echoed.
- Keep config viewer behind admin Basic Auth and avoid piping responses into logs to prevent metadata leaks.

## Storage configuration
- **Local**: defaults to `ORDER_UPLOAD_ROOT=tmp` with files under `orders/{org_id}/{order_id}/...`; mount this path to durable storage or ensure it is backed up.
- **Cloudflare R2/S3-compatible**: set `ORDER_STORAGE_BACKEND=r2` (or `cloudflare_r2`) with `R2_BUCKET`, `R2_ACCESS_KEY`, `R2_SECRET_KEY`, optional `R2_ENDPOINT`/`R2_REGION`. Downloads use presigned GETs honoring `photo_url_ttl_seconds`.
- **Cloudflare Images**: set `ORDER_STORAGE_BACKEND=cloudflare_images` (or `cf_images`) with `CF_IMAGES_ACCOUNT_ID`, `CF_IMAGES_ACCOUNT_HASH`, `CF_IMAGES_API_TOKEN`, and `CF_IMAGES_SIGNING_KEY`; variants controlled by `CF_IMAGES_DEFAULT_VARIANT`/`CF_IMAGES_THUMBNAIL_VARIANT` and delivered via signed exp/sig redirects.
- **Delivery policy**: all backends require app-minted tokens (UA binding and one-time Redis optional) before issuing redirects; do not expose permanent public bucket URLs in admin/worker/client views.
