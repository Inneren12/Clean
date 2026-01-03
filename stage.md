# Stage and Readiness

## Current stage
- **Status:** Production-ready MVP with Conditional GO (see `release_assessment.md`).
- **Done:** Estimator, lead intake with captcha/referrals/export, slot/booking creation with deposit policy, Stripe webhook, email reminders/resend, admin metrics CSV, retention cleanup endpoints, worker portal with checklists/time tracking, photo uploads + admin review/feedback with signed-download redirects (R2/CF Images), SaaS auth + billing plans, rate limiting and CORS controls.
- **Blocked/Risks:** Operators must wire schedulers for cleanup/email/export/retention, configure Stripe/email/export credentials, and set CORS/proxy trust lists in production.
- **Next milestones:** Harden SaaS billing/usage reporting, expand DLQ self-healing (after replay endpoint), and wire dashboarding for job error counters/storage janitor retries. Admin productivity Sprints 11–15 shipped (global search, scheduling controls, time tracking surface area, messaging previews/resend, safe CSV + bulk actions).
- **Sprint 1 (Security baseline):** DONE – admin middleware reordered to isolate `/v1/admin/*`, org-scoped finance/report/export/payment endpoints, and regression tests for cross-org leakage.

## Production readiness gates (must stay green)
- ✅ Tests and migrations: `make test`, `pytest -m "migrations"`, and Alembic head matches `/readyz`.
- ✅ Migration hygiene: CI fails fast if `alembic heads` returns more than one revision—add a merge migration before merging.
- ✅ Migrations applied and `alembic_version` matches `alembic/versions` head.
- ✅ Backups: Postgres backup + restore drill validated for tenant data (org_id scoped).
- ✅ Config secrets: non-default secrets for auth tokens, portal secrets, metrics token, storage signing, Stripe/email keys; at least one admin Basic Auth pair configured.
- ✅ Job heartbeat: `/readyz` shows recent heartbeat when `JOB_HEARTBEAT_REQUIRED=true`.
- ✅ Alerts/metrics: Prometheus alerting wired (`ops/prometheus/alerts.yml`), HTTP metrics enabled, error rates monitored.
- ✅ Storage: delete retries running via `storage_janitor` job; upload size/MIME limits enforced.
- ✅ CORS/proxy: `STRICT_CORS=true` with explicit origins; trusted proxy IPs/CIDRs set if behind proxy.

## Known risks and mitigations
- **Scheduler gaps:** If cleanup/reminder/export jobs are not scheduled, stale bookings/emails accumulate; mitigate by wiring cron/Scheduler and monitoring job heartbeat.
- **Stripe outage/circuit trips:** Deposit creation or billing may fail; retries exist but preserve DB consistency—surface errors to clients and alert on repeated failures.
- **Export webhook failures:** Dead letters accumulate; operators must review `GET /v1/admin/export-dead-letter` and use `POST /v1/admin/export-dead-letter/{id}/replay` after fixing the target.
- **Storage limits:** Entitlements enforce per-plan bytes; ensure `storage_janitor` runs and `order_photo_max_bytes` tuned.

## Release checklist (copy/paste)
- [ ] Set environment: `APP_ENV=prod`, `STRICT_CORS=true`, `CORS_ORIGINS=[...]`, proxy trust lists configured.
- [ ] Configure secrets: auth/portal/photo/metrics secrets, admin credentials, Stripe keys + webhook secret, email/SMTP or SendGrid keys.
- [ ] Run DB: apply migrations via `make migrate`; verify `alembic_version` matches `alembic heads`.
- [ ] Seed pricing config and verify estimator response.
- [ ] Start jobs: schedule cleanup/email/retention/export tasks; enable `job_heartbeat_required` if monitoring heartbeats.
- [ ] Verify health: `/healthz` returns ok; `/readyz` shows DB+migrations OK and job heartbeat fresh.
- [ ] Exercise smokes: lead intake with captcha (if enabled), booking with deposit to Stripe webhook, admin metrics CSV, worker photo upload + signed URL access.
- [ ] Confirm backups/restore runbook executed.
