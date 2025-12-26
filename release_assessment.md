# Release Readiness Review

## 1) Executive summary
- **Classification:** v1
- **Verdict:** Conditional GO
- **Conditions:** release gates in `docs/release_gates.md` are green; production/staging secrets and CORS origins are populated; schedulers or cron wiring are in place for cleanup/export/email/retention jobs.

## 2) Inventory (what is shipped)
- **Public endpoints:** `GET /healthz`, `POST /v1/estimate`, `POST /v1/chat/turn`, `POST /v1/leads` (optional Turnstile), `GET /v1/slots`, `POST /v1/bookings`, `POST /v1/stripe/webhook`.
- **Admin endpoints (Basic auth, admin-only):** `POST /v1/admin/retention/cleanup`, `POST /v1/admin/email-scan`, `POST /v1/admin/bookings/{id}/resend-last-email`, `POST /v1/admin/pricing/reload`, `GET /v1/admin/metrics`, `GET /v1/admin/export-dead-letter`.
- **Dispatcher-allowed endpoints (Basic auth; admin also allowed):** `GET /v1/admin/leads`, `POST /v1/admin/leads/{id}/status`, `GET /v1/admin/bookings`, `POST /v1/admin/bookings/{id}/confirm`, `POST /v1/admin/bookings/{id}/cancel`, `POST /v1/admin/bookings/{id}/reschedule`, `POST /v1/admin/bookings/{id}/complete`, `POST /v1/admin/cleanup`.
- **Scheduler/cron targets:** `/v1/admin/cleanup`, `/v1/admin/email-scan`, `/v1/admin/retention/cleanup`, `/v1/admin/export-dead-letter`.

## 3) Release gates
- See `docs/release_gates.md` for copy-paste commands covering backend migrations/tests, web build, and smokes for admin/dispatcher auth and CORS preflight.

## 4) Evidence matrix
| Area | Status | Evidence (endpoints + files + tests + docs) | Remaining gaps |
| --- | --- | --- | --- |
| Estimator | ✅ | `/v1/estimate` in `app/api/routes_estimate.py`; estimator logic/unit tests in `tests/test_estimator.py` and API coverage in `tests/test_estimate_api.py`. | None beyond keeping pricing file immutable. |
| Leads + captcha | ✅ | `/v1/leads` with Turnstile gating in `app/api/routes_leads.py`; coverage in `tests/test_leads.py` and `tests/test_captcha.py`. | Requires Turnstile secrets when captcha mode is on; ensure export/email providers configured. |
| Slots & bookings | ✅ | Slot search and booking creation in `app/api/routes_bookings.py`; booking flow covered by `tests/test_slots.py` and admin booking ops in `tests/test_admin_api.py`. | Stale pending cleanup requires scheduled calls to `/v1/admin/cleanup`. |
| Deposits + Stripe webhook | ✅ | Deposit policy and webhook handling in `app/api/routes_bookings.py`; scenarios for required/optional deposits and webhook events in `tests/test_deposits.py`. | Stripe keys/webhook secret must be set; webhook retry remains manual. |
| Email workflow | ✅ | Pending email send plus reminder scan/resend in `app/api/routes_bookings.py` and `app/api/routes_admin.py`; validated by `tests/test_email_workflow.py`. | Outbound adapter must be configured; scheduler needed for reminder scan. |
| Analytics & metrics CSV | ✅ | Event logging in lead/booking handlers; metrics CSV via `GET /v1/admin/metrics` in `app/api/routes_admin.py`; exercised in `tests/test_admin_metrics.py`. | Align downstream dashboard with CSV schema. |
| Referrals (credit on confirm/paid) | ✅ | Referral issuance/validation in `app/api/routes_leads.py`; credit on confirmation/deposit in `app/api/routes_admin.py`; tested in `tests/test_referrals.py`. | None beyond monitoring credit issuance. |
| Export + dead-letter API | ⚠️ | Export dispatch and allowlist enforcement in `app/api/routes_leads.py`; dead-letter listing in `app/api/routes_admin.py`; covered in `tests/test_export.py` and `tests/test_export_dead_letter.py`. | No automated retry/processor; operators must review dead letters and replay manually. |
| Retention cleanup | ⚠️ | Retention configuration in `app/settings.py`; cleanup endpoint in `app/api/routes_admin.py`; flow validated in `tests/test_retention.py`. | Requires scheduler/cron wiring to run periodically. |
| Rate limiting (Redis or in-memory) | ✅ | Middleware and Redis fallback in `app/main.py` and `app/infra/security.py`; behavior verified by `tests/test_rate_limit_proxy.py` and `tests/test_rate_limiter.py`. | Ensure trusted proxy CIDRs/IPs set in production. |
| CORS | ✅ | CORS origins derived from settings in `app/main.py`; allowed-origin/preflight behavior covered by `tests/test_cors.py`. | Must set `STRICT_CORS=true` and specify `CORS_ORIGINS` per environment. |
| Admin/dispatcher roles | ✅ | Role checks and endpoint scopes in `app/api/routes_admin.py`; dispatcher restrictions asserted in `tests/test_admin_api.py` and `tests/test_dispatcher_admin_block.py`. | Rotate Basic auth credentials; add auditing if needed. |
| Web UI (`/` and `/admin`) | ⚠️ | Next.js pages in `web/app/page.tsx` and `web/app/admin/page.tsx`; build validated by CI `npm run build`. | No lint/e2e coverage; manual checks recommended on staging. |
| Cloudflare baseline | ⚠️ | Deploy/runbook documented in `docs/cloudflare.md`; env matrix mirrors `app/settings.py`. | Schedulers/backups/monitoring not automated; CORS/proxy CIDRs must be set during deploy. |

## 5) Risk register
- **Code risks:** None blocking v1 identified.
- **Ops risks:** Missing schedulers for cleanup/email/retention/export; dependency on configured Stripe/email/export credentials; need CORS origins and trusted proxy ranges tightened in production; backups/restore and monitoring not yet rehearsed.

## 6) Decision & next steps
- **Operator checklist for Conditional GO:**
  - Apply release gates in `docs/release_gates.md` (backend migrations/tests, web build, smokes for admin/dispatcher auth and CORS preflight).
  - Populate environment secrets for database, admin/dispatcher auth, pricing path, CORS, Stripe, email/export providers, captcha, and proxy trust lists.
  - Wire schedulers/Cron/Cloudflare Scheduler for `/v1/admin/cleanup`, `/v1/admin/email-scan`, `/v1/admin/retention/cleanup`, and `/v1/admin/export-dead-letter`.
  - Perform rollback drill (previous container tag) and ensure backup/restore and monitoring alerts are in place before promotion.
