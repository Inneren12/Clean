# Release Readiness Review v1

## 1) Executive summary
- **Recommendation:** NO-GO for v1; classify as **MVP** due to missing automated tests, unproven CI, and incomplete operational playbooks (backup/monitoring/Cloudflare deploy steps not runnable).
- **Rationale:** Core flows exist (estimate → chat → lead → booking/deposit → admin ops/analytics) but lack automated verification and documented runbooks for day-2 ops and Cloudflare deployments. Admin/dispatcher auth is config-only without explicit CORS/secret checklists, and email/export modes default to "off".

## 2) Release Gates (reference commands; not run)
- Backend install & migrate: `make up` → `make migrate` (expects Docker, Postgres, .env).【F:README.md†L6-L36】
- Backend tests: `make test` / `pytest -q` (no documented coverage).【F:Makefile†L1-L35】
- Backend server smoke: `curl http://localhost:8000/healthz` and sample POSTs to `/v1/estimate`, `/v1/chat/turn`, `/v1/leads`.【F:README.md†L34-L89】
- Frontend: `cd web && npm install && npm run dev` (no lint/build gate noted).【F:README.md†L142-L152】
- DB migrations: `alembic upgrade head` (Dockerized via make).【F:README.md†L18-L29】【F:Makefile†L13-L23】
- CI: No workflow outcomes documented; manual verification needed.
- Smoke admin endpoints: `/v1/admin/leads`, `/v1/admin/metrics`, `/v1/admin/export-dead-letter`, `/v1/admin/email-scan`, `/v1/admin/cleanup` (basic auth required).【F:app/api/routes_admin.py†L37-L93】【F:app/api/routes_admin.py†L124-L197】

## 3) Feature matrix
| Feature | Status | Evidence | Test/Doc coverage | Notes |
| --- | --- | --- | --- | --- |
| Chat parsing → state machine → responder | ⚠️ Partial | Routers include `/v1/chat/turn`; chat state machine in `app/domain/chat/*` (not exercised in docs). | No tests referenced; README only mentions sample call.【F:README.md†L60-L73】 | Needs automated conversation tests and edge cases (PII redaction?). |
| Estimator (pricing config) | ✅ Complete | Estimator load and reload via `/v1/admin/pricing/reload`; settings load config file path default `pricing/economy_v1.json`.【F:app/settings.py†L26-L72】【F:app/api/routes_admin.py†L175-L188】 | No automated tests listed; manual curl example in README.【F:README.md†L34-L73】 | Ensure pricing hash stability and protected from edits in prod. |
| Lead intake & referral attribution | ✅ Complete | `/v1/leads` with referral code flow per README; admin status transitions and listing in `routes_admin`.【F:README.md†L74-L115】【F:app/api/routes_admin.py†L94-L170】 | No tests; docs in README. | Need validation/PII handling checks in logging middleware.【F:app/main.py†L46-L86】 |
| Booking slots & creation | ✅ Complete | Slot search/booking described in README; bookings routes include admin list/confirm/cancel/reschedule/complete endpoints.【F:README.md†L96-L141】【F:app/api/routes_admin.py†L188-L278】 | No automated tests noted. | Depends on timezone logic and pending cleanup job (cron via admin cleanup). |
| Deposit policy & checkout/webhook | ⚠️ Partial | Policy described in README; Stripe keys configured via settings; bookings admin confirm handles referral credit on confirmation.【F:README.md†L114-L141】【F:app/settings.py†L63-L77】【F:app/api/routes_admin.py†L222-L266】 | No webhook tests; no CI validation. | Need live Stripe client wiring and webhook signature verification review. |
| Email workflow (reminders/resend/dedupe) | ⚠️ Partial | Admin `email-scan` and `resend-last-email` endpoints exist using `email_service`.【F:app/api/routes_admin.py†L124-L159】 | No test coverage; docs absent beyond README. | Email adapter default off; requires SendGrid/SMTP config. |
| Analytics & metrics CSV | ✅ Complete | `/v1/admin/metrics` with CSV option plus event logging helper usage during confirmations.【F:app/api/routes_admin.py†L200-L248】 | No tests; relies on analytics service. | Need data validation for missing events. |
| Export & dead-letter handling | ⚠️ Partial | `export_mode` settings with webhook allowlist/private IP flags; admin dead-letter list endpoint.【F:app/settings.py†L42-L58】【F:app/api/routes_admin.py†L160-L173】 | No tests; operations unclear. | Need retry scheduler/runbook. |
| Retention cleanup | ⚠️ Partial | Admin endpoint `/v1/admin/retention/cleanup`; settings for chat/lead retention toggles.【F:app/api/routes_admin.py†L174-L187】【F:app/settings.py†L55-L62】 | No scheduling docs/tests. | Requires cron/Cloudflare Scheduler guidance. |
| Admin/dispatcher auth & role restrictions | ✅ Complete | Basic auth verification shared; admin-only guard for sensitive routes.【F:app/api/routes_admin.py†L37-L93】【F:app/api/routes_admin.py†L200-L217】 | No automated auth tests. | Needs secret rotation guidance. |
| Frontend chat tester | ⚠️ Partial | Next.js app setup documented, but no build/lint scripts in release gates. Environment variable required.【F:README.md†L142-L152】 | No tests; minimal UI. | Upgrade Next.js version and add prod build pipeline.

## 4) Risk register
- **P0: Missing automated test coverage/CI** – No pytest or frontend build steps in release gates; regressions likely. Mitigation: add pytest suites for endpoints and configure GitHub Actions to run tests on PRs.【F:Makefile†L27-L32】
- **P0: Deployment/Cloudflare steps unspecified** – Cloudflare Pages/Containers docs exist but not validated; CORS/secret handling undefined leading to broken prod deploys. Mitigation: expand docs with env var matrix and deploy checklist.
- **P1: Rate limiting/PII logging** – Logging middleware records path/status but may include request IDs only; need assurance of no body logging and rate limiter fail-open behavior with Redis defaults (`redis_url` optional). Mitigation: document limiter dependency and add tests for denial path.【F:app/main.py†L46-L86】【F:app/settings.py†L11-L20】
- **P1: Stripe webhook and deposit flow not exercised** – Risk of incomplete signature validation or state transitions. Mitigation: add integration tests and dry-run doc for webhook secrets.【F:app/settings.py†L63-L77】
- **P2: Ops runbook gaps (retention/export/email)** – No scheduling/monitoring guidance; dead-letter processing manual. Mitigation: add runbook steps and sample cron/Cloudflare Scheduler configs.【F:app/api/routes_admin.py†L160-L187】

## 5) Deployment readiness
- **Cloudflare Pages/Containers:** Docs exist but need validation; ensure API base URL and env secrets align (not covered by Makefile).【F:README.md†L142-L152】
- **Env vars (critical):** DATABASE_URL, REDIS_URL (for rate limit), ADMIN/dispatcher creds, CORS_ORIGINS/STRICT_CORS, EMAIL_MODE & provider keys, EXPORT_* allowlist, STRIPE_* keys/URLs, RETENTION_* toggles, PRICING_CONFIG_PATH.【F:app/settings.py†L9-L77】
- **CORS checklist:** Strict mode defaults to blocking unless dev; `_resolve_cors_origins` allows localhost in dev but empty list otherwise—ensure production origins set or API will reject browsers.【F:app/main.py†L63-L82】
- **Rollback:** Docker Compose allows `make down` and DB persistence via volume; no migration rollback guidance—add backup/restore steps.

## 6) Recommended next PR sequence
1. **Add CI workflows for backend tests** – Create GitHub Actions running `pip install -r requirements.txt`, `pytest`, and Alembic lint; touch `.github/workflows/ci.yml`, `requirements*.txt`. Acceptance: green run on PR with failing tests blocking merges.
2. **Backend test suite coverage** – Add pytest cases for `/healthz`, `/v1/estimate`, `/v1/leads`, admin auth guards, and rate-limit rejection; touch `tests/` with factory fixtures. Acceptance: deterministic pass locally and in CI.
3. **Stripe deposit/webhook validation** – Add tests and docs ensuring signature verification and booking state transitions; touch `app/api/routes_bookings.py`, `app/infra/stripe.py`, `docs/runbook_pilot.md`. Acceptance: webhook test proving paid/expired paths handled.
4. **Operational runbook & scheduling** – Extend `docs/runbook_pilot.md` and Cloudflare docs with scheduler examples for retention cleanup/email scan/export retries; include env var matrix and backup expectations. Acceptance: step-by-step operator playbook.
5. **Frontend build/lint pipeline** – Add `npm run lint`/`npm run build` scripts to CI and fix blocking issues; touch `web/package.json`, `.github/workflows/ci.yml`. Acceptance: CI enforces lint/build and README updated.
6. **Export dead-letter handling** – Implement retry job or manual script; document in docs/export_dead_letter; touch `app/infra/export.py`, `docs/export_dead_letter.md`. Acceptance: admin endpoint shows cleared retries after job.
