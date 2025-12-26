# Release Readiness Review v1

## 1) Executive summary
- **Recommendation:** Conditional **GO for v1** once the documented release gates (Section 4) are green and secrets/CORS origins are populated for the target environment. Automated coverage exists for estimator, chat→lead, deposits, referrals, retention, and admin auth, and CI runs both migrations and the web build on PRs. Remaining risks are operational (scheduler setup, backups/rollback drills, email/export providers) rather than code gaps.
- **MVP vs v1 decision:** The codebase meets v1 bar with tests and CI in place; the remaining work is procedural: configure production env vars (Stripe/email/export/CORS), stand up schedulers for cleanup/email/retention, and finalize backups/monitoring per Section 5.

## 2) Inventory (what is shipped)
- **Public APIs**
  - `GET /healthz` (unauth) – health probe.
  - `POST /v1/estimate` (unauth) – pricing estimator.
  - `POST /v1/chat/turn` (unauth) – chat state + estimator assistant.
  - `POST /v1/leads` (unauth; Turnstile enforced when configured) – lead capture, referral validation, async export/email.
  - `GET /v1/slots` (unauth) – slot search by date/duration.
  - `POST /v1/bookings` (unauth) – create pending booking, apply deposit policy, Stripe checkout when required, pending-email dispatch.
  - `POST /v1/stripe/webhook` (unauth) – verifies signature, marks deposit paid/expired/failed.
- **Admin/dispatcher APIs (HTTP Basic)**
  - Leads: list (`GET /v1/admin/leads`), status transition (`POST /v1/admin/leads/{lead_id}/status`).
  - Email workflow: scan for reminders (`POST /v1/admin/email-scan`), resend last email (`POST /v1/admin/bookings/{booking_id}/resend-last-email`).
  - Export dead-letter: list failed exports (`GET /v1/admin/export-dead-letter`).
  - Retention: cleanup chat/leads when enabled (`POST /v1/admin/retention/cleanup`).
  - Pricing: reload config from path (`POST /v1/admin/pricing/reload`).
  - Bookings ops: list (`GET /v1/admin/bookings`), confirm (`POST /v1/admin/bookings/{booking_id}/confirm`), cancel, reschedule, complete.
  - Cleanup: delete stale pending bookings (`POST /v1/admin/cleanup`).
  - Metrics: conversions/revenue/duration accuracy with CSV option (`GET /v1/admin/metrics`).
- **Background/cron targets**
  - Pending booking cleanup (`/v1/admin/cleanup`).
  - Email reminders scan (`/v1/admin/email-scan`).
  - Retention cleanup (`/v1/admin/retention/cleanup`).
  - Export dead-letter review (`/v1/admin/export-dead-letter`).
- **Critical flows**
  - Estimate → Lead: estimator endpoint feeds chat; `/v1/leads` stores structured inputs/estimate, validates captcha/referral, logs analytics, and triggers export/email.
  - Lead → Booking/Slots: `/v1/slots` supplies availability; `/v1/bookings` creates pending bookings tied to leads.
  - Booking → Deposit → Webhook → Confirm: deposit policy evaluated on create; Stripe checkout session stored; webhook confirms or cancels and dispatches emails; admin confirm also logs analytics and referral credit.
  - Email workflow: pending booking email on create; reminder scan and resend endpoints.
  - Admin/dispatcher ops: guarded by Basic auth with dispatcher vs admin separation.
  - Analytics: event logging on leads/bookings/confirm, metrics endpoint with CSV.
  - Referrals: lead creation issues codes, validates referred_by, and grants credits on confirmation.
  - Hardening: rate limiting (in-memory/Redis), strict/allowlisted CORS, captcha option, export allowlist/private-IP blocking, retention controls.

## 3) Evidence-based completeness matrix
| Area | Status | Evidence | Gaps/Risks |
| --- | --- | --- | --- |
| Chat → estimate → lead persistence | ✅ Complete | Chat turn persists state and returns estimates; integration test covers chat→lead→DB flow.【F:app/api/routes_chat.py†L18-L48】【F:tests/test_e2e_lead_flow.py†L8-L46】 | None blocking; monitor chat DB growth (retention job optional). |
| Estimator & pricing reload | ✅ Complete | `/v1/estimate` uses pricing config; admin reload endpoint exists.【F:app/api/routes_estimate.py†L11-L16】【F:app/api/routes_admin.py†L271-L274】 | Ensure pricing file immutability in prod storage. |
| Lead intake & referral attribution | ✅ Complete | `/v1/leads` validates captcha/referrals, logs analytics, schedules export/email; referral codes unique.【F:app/api/routes_leads.py†L78-L204】 | Export/email depend on provider configuration; add monitoring for failures. |
| Slots & booking creation | ✅ Complete | Public slot search; booking creation reserves slot, logs analytics, emits pending email.【F:app/api/routes_bookings.py†L29-L177】 | Scheduler for stale cleanup required in prod. |
| Deposit policy & Stripe webhook | ✅ Complete | Booking evaluates deposit policy, creates checkout session, webhook marks paid/expired; tested for required deposits and missing Stripe keys.【F:app/api/routes_bookings.py†L48-L235】【F:tests/test_deposits.py†L151-L199】 | Stripe secrets must be set; webhook retries not present (manual). |
| Email workflow (pending + reminders + resend) | ✅ Complete | Email adapter used on booking create; admin scan/resend endpoints; reminder scan wired in service layer.【F:app/api/routes_bookings.py†L158-L176】【F:app/api/routes_admin.py†L129-L161】 | Requires configured adapter; scheduler not documented. |
| Admin/dispatcher auth & ops | ✅ Complete | Basic auth guard with admin-only vs dispatcher; booking status ops/metrics protected.【F:app/api/routes_admin.py†L42-L382】 | Rotate credentials; consider audit logging. |
| Analytics & metrics | ✅ Complete | Event logging on lead/booking/confirm; metrics endpoint with CSV output.【F:app/api/routes_leads.py†L138-L156】【F:app/api/routes_admin.py†L219-L268】 | None blocking; validate dashboard consumer. |
| Export + dead-letter | ⚠️ Partial | Async export scheduled on lead create; admin dead-letter listing.【F:app/api/routes_leads.py†L164-L199】【F:app/api/routes_admin.py†L164-L186】 | No retry processor/runbook; operator must inspect dead letters. |
| Retention & cleanup jobs | ⚠️ Partial | Admin retention cleanup endpoint and pending booking cleanup exist; retention toggles in settings.【F:app/api/routes_admin.py†L188-L194】【F:app/api/routes_bookings.py†L180-L186】【F:app/settings.py†L59-L67】 | No scheduler wiring; cron/Cloudflare Scheduler needs setup. |
| Rate limiting & CORS hardening | ✅ Complete | Middleware enforces rate limits (Redis-aware, fail-open on Redis errors) and strict CORS when configured.【F:app/main.py†L83-L143】【F:app/infra/security.py†L1-L118】 | Ensure trusted proxy ranges set in prod. |
| Frontend build readiness | ⚠️ Partial | Next.js app builds via `npm run build`; CI runs build on PRs.【F:web/package.json†L4-L17】【F:.github/workflows/ci.yml†L62-L76】 | No lint/static checks; minimal UI and no e2e tests. |

## 4) Release gates (must be green)
See `docs/release_gates.md` for exact commands. Summary:
- **Backend:** install deps (`pip install -r requirements.txt`), run Alembic migrations (`alembic upgrade head`), execute pytest suite (`pytest`). CI backend job already runs these against Postgres service on PRs.【F:.github/workflows/ci.yml†L8-L58】
- **Frontend:** `npm ci && npm run build` (CI web job enforces).【F:.github/workflows/ci.yml†L60-L76】
- **Docker compose smoke:** `make up` → `curl http://localhost:8000/healthz` (health probe) before releasing.【F:README.md†L6-L36】
- **Admin auth smoke:** `curl -u $ADMIN_BASIC_USERNAME:$ADMIN_BASIC_PASSWORD /v1/admin/leads` against staging to confirm credentials and CORS.
- **Manual Stripe webhook check:** send signed test webhook using Stripe CLI when deposits enabled.

## 5) Deploy readiness (Cloudflare baseline)
- **Pages vs repo reality:** Cloudflare Pages doc prescribes `@cloudflare/next-on-pages@1` or `next export` and root `web/`, matching the repo’s Next.js app and CI build (no export).【F:docs/cloudflare.md†L6-L34】【F:.github/workflows/ci.yml†L60-L76】
- **Container workflow:** Deploy doc aligns with `deploy_cloudflare.yml` (ECR push inputs/secrets).【F:docs/cloudflare.md†L36-L83】【F:.github/workflows/deploy_cloudflare.yml†L1-L32】
- **Env var matrix:** Cloudflare doc lists API env vars including CORS/STRICT, admin/dispatcher creds, Stripe/email/export, retention, proxy trust, pricing path, matching `app/settings.py` fields.【F:docs/cloudflare.md†L84-L137】【F:app/settings.py†L10-L68】
- **CORS lock:** Strict CORS defaults to empty origins unless dev; Cloudflare checklist calls out preview/prod origins only.【F:app/main.py†L107-L143】【F:docs/cloudflare.md†L139-L158】
- **Ops gaps:** Backups/restore and monitoring not defined for Cloudflare stack; retention/email/cleanup/export schedulers need Cloudflare Scheduler or cron wiring; rollback noted but not rehearsed.【F:docs/cloudflare.md†L160-L188】【F:docs/deploy.md†L47-L61】

## 6) MVP vs v1 decision
**V1 criteria met:** (a) automated tests for core flows (estimates, chat→lead, deposits, referrals, retention, admin auth, migrations) and CI running them; (b) documented deploy steps for Render + Cloudflare; (c) CORS/ratelimit/auth hardening in code. Outstanding items (scheduler wiring, backups/monitoring, Stripe/email secret provisioning) are operational tasks tracked in runbooks rather than code changes, so do not block v1 once release gates pass. If operators cannot staff schedulers/monitoring yet, ship as MVP-only with manual playbooks.
