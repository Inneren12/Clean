# Stage and Readiness

## Current stage
- **Status:** Production-ready MVP with Conditional GO (see `release_assessment.md`).
- **Done:** Estimator, lead intake with captcha/referrals/export, slot/booking creation with deposit policy, Stripe webhook, email reminders/resend, admin metrics CSV, retention cleanup endpoints, worker portal with checklists/time tracking, photo uploads + admin review/feedback with signed-download redirects (R2/CF Images), SaaS auth + billing plans, rate limiting and CORS controls, structured logging with PII/token redaction and per-request request/org/user context.
- **Done:** Estimator, lead intake with captcha/referrals/export, slot/booking creation with deposit policy, Stripe webhook, email reminders/resend, admin metrics CSV, retention cleanup endpoints, worker portal with checklists/time tracking, photo uploads + admin review/feedback with signed-download redirects (R2/CF Images), SaaS auth + billing plans, rate limiting and CORS controls, outbox-based delivery with retries/DLQ and admin replay.
- **Blocked/Risks:** Operators must wire schedulers for cleanup/email/export/retention, configure Stripe/email/export credentials, and set CORS/proxy trust lists in production.
- **Sprint 4–6:** DONE – logging redacts PII and emits `unhandled_exception` with request IDs and identity context while clearing job/outbox log context; canonical storage keys use `orders/{org}/{booking}` prefixes across code/tests; `X-Test-Org` honored only in testing/dev; FastAPI lifespan attaches `AppServices` container with clean shutdown.
- **Sprint A (Admin safety):** DONE – admin/iam surfaces enforce IP allowlisting, an incident-mode read-only gate returning Problem+JSON responses, and org-scoped break-glass tokens (reason + TTL) with audit trails for emergency writes.
- **Operator Productivity Pack (Sprints 20-22 + Release Hardening):** DONE – work queues for pending photos, overdue invoices, unassigned bookings, and dead letter queue (outbox + export); enhanced global search v2 with weighted/ranked results (exact/prefix/contains), worker coverage, and context-aware quick actions (email/call/timeline links); unified timeline view for bookings/invoices aggregating audit logs, outbox events, payments, photo reviews, NPS responses, and support tickets with before/after diffs for critical state changes; all queues org-scoped with pagination and quick action buttons for common operator workflows. **RELEASE-GRADE HARDENED**: strict RBAC enforced (dispatch for photos/assignments, finance for invoices, admin for DLQ, viewer for timeline with PII masking); full org-scoping on all joins (Booking/Worker/Lead/Team); DLQ pagination at SQL level using UNION ALL for scalability; timeline queries use safe patterns (no `LIKE %id%`) with limits and structured prefix patterns; Worker field correctness fixed; PII masking utility for viewer role (emails/phones/sensitive text); comprehensive semantic tests for filters/counts/pagination/cross-org/RBAC/PII (35+ tests total including hardening suite).
- **Updates:** Operator hardening tests fixed for collection (Payment import) and tightened timeline action masking to avoid recipient leakage.
- **Sprint A (Admin safety):** DONE – admin/iam surfaces enforce IP allowlisting and an incident-mode read-only gate returning Problem+JSON responses.
- **Sprint B (Admin safety - idempotency):** DONE – dangerous admin actions (payments, resends/replays, bulk updates, IAM resets) enforce org-scoped `Idempotency-Key` headers with persistent dedupe records, per-action rate limits, and audit logging for replayed responses.
- **Sprint D (Admin safety - MFA):** DONE – SaaS OWNER/ADMIN users can enroll/verify/disable RFC6238 TOTP (`/v1/auth/2fa/*`), sessions carry `mfa_verified`, and `/v1/admin/*` + `/v1/iam/*` reject SaaS admin tokens without MFA when `ADMIN_MFA_REQUIRED=true`. Legacy Basic Auth remains available but is not MFA-capable.
- **Finance Recon A:** DONE – read-only invoice reconciliation views enumerate mismatches between invoice status and succeeded payments for FINANCE roles, scoped per org.
- **Finance Recon B:** DONE – FINANCE-only Stripe event reconciliation view listing org-scoped webhook activity with pagination and metadata (event type, invoice/booking references, status, error reason).
- **Next milestones:** Harden SaaS billing/usage reporting, expand DLQ self-healing (after replay endpoint), and wire dashboarding for job error counters/storage janitor retries. Admin productivity Sprints 11–15 shipped (global search, scheduling controls, time tracking surface area, messaging previews/resend, safe CSV + bulk actions). Sprints 16–19 deliver client self-service (bookings/invoices/photos), subscription pause/resume with reasons, NPS ticket filters, guarded feature-flag/config viewers, and unified Problem+JSON error handling with consistent request IDs.
- **Lifecycle/DI:** FastAPI lifespan now wires a typed `AppServices` container (storage, email, Stripe, rate limiting, metrics) onto `app.state.services` with shutdown cleanup to reduce global-state bugs; deprecated `@app.on_event` hooks removed.
- **Sprint 1 (Security baseline):** DONE – admin middleware reordered to isolate `/v1/admin/*`, org-scoped finance/report/export/payment endpoints, and regression tests for cross-org leakage.
- **P1 (Postgres RLS guardrail):** DONE – org isolation enforced via Postgres RLS on core tables using `app.current_org_id`; keep existing `org_id` filters and follow OPERATIONS RLS verification steps during rollout.

## Production readiness gates (must stay green)
- ✅ Tests and migrations: `make test`, `pytest -m "migrations"`, and Alembic head matches `/readyz`.
- ✅ Migration hygiene: CI fails fast if `alembic heads` returns more than one revision—add a merge migration before merging.
- ✅ Migrations applied and `alembic_version` matches `alembic/versions` head.
- ✅ Org isolation regressions: automated suite seeds multiple orgs and blocks cross-org finance/payments/export/iam/photo access; metrics path labels stay templated to avoid cardinality blow-ups.
- ✅ Backups: Postgres backup + restore drill validated for tenant data (org_id scoped).
- ✅ Config secrets: non-default secrets for auth tokens, portal secrets, metrics token, storage signing, Stripe/email keys; at least one admin Basic Auth pair configured.
- ✅ Job heartbeat: `/readyz` shows recent heartbeat when `JOB_HEARTBEAT_REQUIRED=true`.
- ✅ Alerts/metrics: Prometheus alerting wired (`ops/prometheus/alerts.yml`), HTTP metrics enabled, error rates monitored.
- ✅ Storage: delete retries running via `storage_janitor` job; upload size/MIME limits enforced.
- ✅ CORS/proxy: `STRICT_CORS=true` with explicit origins; trusted proxy IPs/CIDRs set if behind proxy.
- ✅ Email/export delivery: admin scans and jobs resolve adapters from `app.state`/services, send immediately while still
  queuing outbox events for retries and DLQ replay.

## Known risks and mitigations
- **Scheduler gaps:** If cleanup/reminder/export jobs are not scheduled, stale bookings/emails accumulate; mitigate by wiring cron/Scheduler and monitoring job heartbeat.
- **Stripe outage/circuit trips:** Deposit creation or billing may fail; retries exist but preserve DB consistency—surface errors to clients and alert on repeated failures.
- **Export webhook failures:** Dead letters accumulate; operators must review `GET /v1/admin/export-dead-letter` and use `POST /v1/admin/export-dead-letter/{id}/replay` after fixing the target.
- **Storage limits:** Entitlements enforce per-plan bytes; ensure `storage_janitor` runs and `order_photo_max_bytes` tuned.
- **Org context missing under RLS:** Postgres requires `app.current_org_id` per transaction; missing context yields empty results and blocked inserts. Verify API/cron tasks set org context (see OPERATIONS.md) when enabling RLS.

## Release checklist (copy/paste)
- [ ] Set environment: `APP_ENV=prod`, `STRICT_CORS=true`, `CORS_ORIGINS=[...]`, proxy trust lists configured.
- [ ] Configure secrets: auth/portal/photo/metrics secrets, admin credentials, Stripe keys + webhook secret, email/SMTP or SendGrid keys.
- [ ] Run DB: apply migrations via `make migrate`; verify `alembic_version` matches `alembic heads`.
- [ ] Seed pricing config and verify estimator response.
- [ ] Start jobs: schedule cleanup/email/retention/export tasks; enable `job_heartbeat_required` if monitoring heartbeats.
- [ ] Verify health: `/healthz` returns ok; `/readyz` shows DB+migrations OK and job heartbeat fresh.
- [ ] Exercise smokes: lead intake with captcha (if enabled), booking with deposit to Stripe webhook, admin metrics CSV, worker photo upload + signed URL access.
- [ ] Confirm backups/restore runbook executed.
