# Module Map

## API layer (`app/api`)
- **Routers** (`routes_*.py`): request/response schemas, validation, and wiring to domain services. Admin, worker, client, billing, chat, bookings, payments, metrics, orders, and photo token endpoints stay here.
- **Auth & identity**: `saas_auth.py` handles SaaS JWT sessions and injects org/user into `request.state`; `admin_auth.py` enforces Basic Auth roles and auditing for admin/dispatcher/finance/viewer; `worker_auth.py` signs worker tokens and enforces worker access middleware; `org_context.py` resolves org_id for mixed legacy/basic flows; `entitlements.py` enforces per-plan limits.
- **IAM console**: `routes_iam.py` exposes SaaS-admin user lifecycle endpoints (list/create/reset/deactivate/role changes/logout-all) scoped to the caller's organization.
- **Boundary rule:** routers should depend on services in `app/domain/**` and infrastructure adapters in `app/infra/**`, never call storage/DB primitives directly.

## Domain layer (`app/domain`)
- **Business services**: pricing (`pricing/service.py`), leads (`leads/service.py`), bookings and deposits (`bookings/service.py`), orders/photos (`orders/service.py`), invoices (`invoices/service.py`), notifications (`notifications/service.py`), referrals (`referrals` logic in `leads`/`bookings` services), analytics (`analytics/service.py`), time tracking (`time_tracking/service.py`), and bot/chat (`chat/service.py`, `bot/service.py`).
- **SaaS & billing**: org membership, plans, usage, and session handling (`saas/service.py`, `saas/billing_service.py`, `saas/plans.py`).
- **Operational tooling**: org-scoped search with weighted results and worker coverage, scheduling/bulk/messaging helpers (`ops/service.py`, `ops/schemas.py`).
- **Operator productivity**: work queue queries for photos/invoices/assignments/DLQ (`queues/service.py`, `queues/schemas.py`), unified timeline aggregation for bookings/invoices combining audit logs, outbox events, payments, photo reviews, NPS, support tickets (`timeline/service.py`, `timeline/schemas.py`).
- **Boundaries:** domain code should be storage-agnostic, use repository/data-access helpers in `app/infra/models.py` and DB sessions injected via dependencies.

## Infrastructure layer (`app/infra`)
- **Database and ORM**: SQLAlchemy base/models and session factory (`db.py`, `models.py`). Alembic migrations live in `alembic/` with env in `alembic/env.py`.
- **Auth primitives**: password hashing and token utilities (`auth.py`), CSRF helpers (`csrf.py`).
- **Email and export**: adapters and factories (`email.py`, `export.py`).
- **Security**: rate limiter and client key resolution (`security.py`), captcha verification (`captcha.py`).
- **Stripe**: resilient client and circuit breaker wrappers (`stripe_client.py`, `stripe_resilience.py`, `stripe.py`).
- **Storage**: backends/factory for photos (`storage/__init__.py`, `storage/backends.py`) covering local, S3/R2 presigned GETs, and Cloudflare Images signed delivery variants.
- **Metrics & logging**: `metrics.py` instruments middleware and jobs; `logging.py` configures structured logging; `i18n.py` handles localized strings.
- **Service container**: `app/services.py` builds the `AppServices` dataclass (storage, email adapter, Stripe client, rate limiter, metrics) that FastAPI attaches to `app.state.services` during lifespan startup.

## Shared utilities (`app/shared`)
- Lightweight helpers that are environment-agnostic (e.g., `circuit_breaker.py`, `naming.py`). Keep this free of domain-specific logic.

## Jobs (`app/jobs`)
- Cron-invoked tasks for email/reminders, storage cleanup, and heartbeat publishing (`run.py`, `email_jobs.py`, `storage_janitor.py`, `heartbeat.py`).

## Web frontend (`web/`)
- Minimal Next.js chat/admin test UI. Keep backend-specific logic in API; front-end consumes HTTP endpoints only.

## Boundary guardrails
- Domain services should own business decisions; routers should not bypass service methods to mutate DB state.
- Cross-module calls should respect responsibilities: API ↔ Domain ↔ Infra only. Avoid domain-to-domain shortcuts unless via shared helpers.
- Auth/org resolution must go through `saas_auth.py`, `admin_auth.py`, `worker_auth.py`, and `org_context.py`; do not duplicate header parsing.
- Storage/email/Stripe should be accessed via their factories/adapters in `app/infra/**` to keep swapping possible.
