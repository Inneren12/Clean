# File Overview (top entrypoints)

## Application wiring
- `app/main.py` – FastAPI app factory, middleware stack (request id → rate limit → metrics → logging → security headers → tenant/password gates → admin/worker middleware), router registration, error handlers.
- `app/services.py` – Typed `AppServices` container for storage, email, Stripe, rate limiter, and metrics attached to `app.state.services` during FastAPI lifespan startup.
- `app/dependencies.py` – Common FastAPI dependencies (settings, rate limiter, metrics, export/email resolvers).
- `app/settings.py` – Pydantic settings for envs, auth, storage, Stripe, email, rate limiting, retention, metrics, and default org.

## Auth, identity, and org scoping
- `app/api/saas_auth.py` – SaaS JWT auth, session validation, `TenantSessionMiddleware`, password-change gate, admin role mapping.
- `app/api/org_context.py` – Resolves `org_id` from SaaS identity or defaults; raises 401 when required.
- `app/api/entitlements.py` – Enforces per-org plan limits (workers/bookings/storage), records usage, supports `X-Test-Org` in testing.
- `app/api/admin_auth.py` – Basic Auth roles (owner/admin/dispatcher/finance/viewer), admin auditing middleware, permission helpers.
- `app/api/worker_auth.py` – Worker token issuance/verification, portal session TTLs, and worker access middleware.
- `app/api/routes_auth.py` – SaaS login/refresh/logout/me/password change endpoints and session lifecycle.
- `app/api/routes_iam.py` – Org-scoped IAM console: list/create/deactivate users, reset temp passwords, update roles, revoke sessions.

## Core flows (routers)
- `app/api/routes_estimate.py` – Pricing estimator endpoint.
- `app/api/routes_leads.py` – Lead intake, Turnstile captcha gating, referral codes, export dispatch, admin lead list/status transitions.
- `app/api/routes_bookings.py` – Slot search, booking creation with deposit policy, reschedule/cancel/confirm/complete, reminders.
- `app/api/routes_payments.py` – Stripe invoice checkout/portal, invoice token validation, payment status updates.
- `app/api/routes_billing.py` – SaaS subscription billing (plans, customer portal, metered usage) behind SaaS auth.
- `app/api/routes_orders.py` – Order photo upload/list/delete/review with tokenized signed-download redirects and storage backend selection.
- `app/api/routes_checklists.py` – Worker/admin checklist templates and completion state.
- `app/api/routes_worker.py` – Worker portal endpoints (auth, jobs list, job detail, uploads, status updates).
- `app/api/routes_time_tracking.py` – Time tracking for workers and admin reviews.
- `app/api/routes_admin.py` – Admin utilities: pricing reload, email scan/resend, cleanup, retention, export dead-letter, global search, scheduling actions, messaging previews, and bulk booking updates.
- `app/api/routes_metrics.py` – Metrics CSV/API for admin basic auth.
- `app/api/routes_public.py` – Public landing/docs endpoints.
- `app/api/routes_health.py` – `/healthz` and `/readyz` checks (DB/migrations/job heartbeat).
- `app/api/routes_bot.py` and `app/api/routes_chat.py` – Chat session handling and bot responses.

## Domain services and models
- `app/domain/pricing/service.py` – Price calculation logic backed by JSON config.
- `app/domain/leads/service.py` – Lead creation, referral validation, export dispatch hooks.
- `app/domain/bookings/service.py` – Booking creation/update, deposit rules, slot locking, email triggers.
- `app/domain/orders/service.py` – Order photo metadata, signed URLs, storage delete retries.
- `app/domain/invoices/service.py` – Invoice creation, totals, and Stripe invoice linking.
- `app/domain/notifications/service.py` – Email templates and dispatch orchestration.
- `app/domain/outbox/service.py` – Org-scoped outbox enqueue + delivery with retries/backoff for email/webhook/export payloads.
- `app/domain/analytics/service.py` – Event log recording and metrics aggregation.
- `app/domain/saas/service.py` – SaaS user/org lifecycle, sessions, password reset, membership roles.
- `app/domain/saas/billing_service.py` – Plan usage tracking, metered billing records, entitlements.
- `app/domain/time_tracking/service.py` – Worker check-in/out and admin overrides.
- `app/domain/ops/service.py` – Org-scoped search, scheduling conflict checks, messaging previews, and bulk booking helpers.
- `app/domain/ops/db_models.py` – Operational tables (job heartbeat, email events, exports, retention).
- `app/infra/models.py` – SQLAlchemy models for core entities (users, orgs, leads, bookings, orders, invoices, workers, referrals, billing usage).

## Infrastructure and adapters
- `app/infra/db.py` – Async engine/session factory, base model registration, pool settings.
- `alembic/env.py` – Migration context setup and metadata registration; `alembic/versions/` for schema history.
- `app/infra/security.py` – Rate limiter (Redis or in-memory) and client key extraction with proxy trust options.
- `app/infra/email.py` – SendGrid/SMTP/no-op adapter factory with retries, circuit breaker configuration, and helpers to
  resolve the runtime adapter from `app.state` or injected services.
- `app/infra/stripe_client.py` and `app/infra/stripe_resilience.py` – Stripe client with circuit breaker and retry policies.
- `app/infra/storage/__init__.py` & `app/infra/storage/backends.py` – Storage backend factory for local/S3/R2/Cloudflare Images and signed URL helpers.
- `app/infra/export.py` – Webhook/SaaS export transport with allowlist enforcement and retries.
- `app/domain/outbox/db_models.py` – Outbox persistence for async delivery attempts.
- `app/infra/captcha.py` – Cloudflare Turnstile verification helper.
- `app/infra/metrics.py` – Metrics client and recording helpers for HTTP and jobs; used by middleware and jobs.
- `app/infra/logging.py` – Structured logging setup for request/job logs.
- `app/domain/ops/schemas.py` – Pydantic responses for ops-facing endpoints (job status, monitoring).

## Background jobs and ops
- `app/jobs/run.py` – Entry to invoke scheduled jobs (email scan, retention cleanup, storage delete retries, outbox delivery).
- `app/jobs/email_jobs.py` – Email reminder/scan logic for bookings.
- `app/jobs/outbox.py` – Batch processor for due outbox events.
- `app/jobs/storage_janitor.py` – Retry deletions for storage backends.
- `app/jobs/heartbeat.py` – Record job heartbeat into DB and metrics.
- `docker-compose.yml` – Dev stack for API + Postgres + Redis.
- `.github/workflows/ci.yml` – Test pipeline running lint/tests/migrations; `load-smoke.yml` for load smoke; `deploy_cloudflare.yml` for deployment.
- `ops/prometheus/alerts.yml` – Prometheus alert examples for latency/errors/health/jobs/DLQ.
- `scripts/backup_pg.sh` / `scripts/restore_pg.sh` – Postgres backup/restore helpers (custom-format dumps, no `--create` by default; `ALLOW_CREATE_IN_DUMP` gate for create-in-dump restores).
- Runbooks: `docs/runbook_monitoring.md`, `docs/runbook_incidents.md`, `docs/runbook_backup_restore.md` for ops procedures.

## Frontend
- `web/app/page.tsx` – Chat UI entry.
- `web/app/admin/page.tsx` – Minimal admin/booking UI.
- `web/` `.env.example` – Frontend environment variables for API base URL.

## Tests
- `tests/test_photo_delivery.py` – Signed download redirect/no-store assertions for photo URLs.
