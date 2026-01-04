# Project Overview

## What this product does
Cleaning Economy Bot is a FastAPI backend for booking residential cleaning work, estimating jobs, taking deposits, and running SaaS-style multi-tenant admin and worker portals. Core personas:
- **Admin/Owner/Dispatcher** – manage leads, bookings, schedules, email workflows, retention cleanup, and exports via authenticated admin routes.
- **Finance** – view metrics, invoices, billing plans, and Stripe payments.
- **Worker** – access checklists, time tracking, and upload job photos.
- **Client** – receive booking updates, invoices, and portal access.
- **SaaS user** – organization-scoped admin/dispatcher/finance/viewer accounts authenticated by JWT sessions.

Key flows are implemented in the API routers under `app/api/`:
- **Lead → Booking → Invoice → Payment → Photos** – leads and estimates (`routes_leads.py`, `routes_estimate.py`), slot search + booking + deposit checkout + webhook confirmation (`routes_bookings.py`, `routes_payments.py`, `routes_billing.py`), invoices and Stripe invoice links (`routes_payments.py`), and order photo uploads/listing (`routes_orders.py`, `photo_tokens.py`, storage backends in `app/infra/storage`).
- **Worker upload → Admin review → Worker feedback** – workers authenticate via signed tokens (`worker_auth.py`), capture time-tracking events (`routes_time_tracking.py`), complete checklist items and order photos (`routes_worker.py`, `routes_checklists.py`, `routes_orders.py`), while admins can review and resend email notifications (`routes_admin.py`, `routes_bookings.py`).
- **Operator productivity** – work queues for pending photos/overdue invoices/unassigned bookings/DLQ (`routes_queues.py`, `app/domain/queues`), enhanced global search with weighted results and worker coverage (`routes_admin.py`, `app/domain/ops/service.py`), and unified timeline views combining audit logs/outbox events/payments/photo reviews/NPS tickets (`routes_timeline.py`, `app/domain/timeline`).

## Environments and dependencies
- **Environments:** `APP_ENV` is `dev` or `prod` (`app/settings.py`). Docker Compose (`docker-compose.yml`) runs Postgres and the API; CI spins services via `.github/workflows/ci.yml`.
- **Core dependencies:**
  - **Postgres** via SQLAlchemy async + Alembic (`app/infra/db.py`, `alembic/`).
  - **Redis (optional)** for rate limiting; falls back to in-memory limiter (`app/infra/security.py`).
  - **Stripe** for deposits, invoices, and billing subscriptions (`app/infra/stripe_client.py`, `app/api/routes_bookings.py`, `app/api/routes_payments.py`, `app/api/routes_billing.py`).
  - **Email** adapters for SendGrid/SMTP or disabled mode (`app/infra/email.py`).
  - **Storage** backends for order photos: local, S3/R2, Cloudflare Images, or in-memory (`app/infra/storage/__init__.py`).
  - **Captcha** Turnstile support for leads (`app/infra/captcha.py`, `app/api/routes_leads.py`).

## Multi-tenant model
- **Org resolution:** `TenantSessionMiddleware` and `require_org_context` set `request.state.current_org_id` from SaaS JWTs or fallback `default_org_id` (`app/api/saas_auth.py`, `app/api/org_context.py`).
- **Scoping:** Entitlement checks enforce per-org limits for workers, bookings, and storage (`app/api/entitlements.py`). Domain models include `org_id` on SaaS users, memberships, and billing usage (`app/domain/saas/db_models.py`, `app/domain/saas/billing_service.py`).
- **Testing override:** `X-Test-Org` header allows injecting org context in testing mode (`app/api/entitlements.py`).

## Operational behaviors
- Health and readiness: `/healthz` lightweight, `/readyz` checks DB connectivity, migration head, and job heartbeat (`app/api/routes_health.py`).
- Metrics and logging: middleware registers latency/5xx metrics and request logs (`app/main.py`, `app/infra/metrics.py`, `app/infra/logging.py`).
- Jobs: email/reminder/cleanup/export/retention jobs run via `app/jobs/run.py` and heartbeat tracked in `app/jobs/heartbeat.py` with DB record `JobHeartbeat`.

## Read order
1. `PROJECT_OVERVIEW.md` (this file)
2. `MODULES.md` (boundaries and responsibilities)
3. `FILE_OVERVIEW.md` (entrypoints and top files)
4. `CONTENT_GUIDE.md` (change rules)
5. `stage.md` (current stage and gates)
6. Optional: `SECURITY_MODEL.md`, `OPERATIONS.md`, `TESTING.md` for deeper dives.
