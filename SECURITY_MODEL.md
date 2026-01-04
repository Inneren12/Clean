# Security Model

## Authentication mechanisms
- **Admin/Dispatcher/Finance/Viewer Basic Auth**: enforced by `AdminAccessMiddleware` with per-role permissions and auditing (`app/api/admin_auth.py`). Credentials come from env vars (owner/admin/dispatcher/accountant/viewer pairs).
- **Admin safety gates**: `/v1/admin/*` and `/v1/iam/*` run through `AdminSafetyMiddleware` for two controls: (1) `ADMIN_IP_ALLOWLIST_CIDRS` enforces CIDR allowlisting using trusted proxy resolution, returning 403 Problem+JSON on mismatch; (2) `ADMIN_READ_ONLY=true` blocks POST/PUT/PATCH/DELETE with a 409 Problem+JSON, keeping read-only GETs available during incidents.
- **SaaS JWT (org-scoped)**: access tokens validated by `TenantSessionMiddleware`; sessions stored in DB and refreshed via `/v1/auth/refresh` (`app/api/saas_auth.py`, `app/api/routes_auth.py`). `require_org_context` enforces org presence when SaaS tokens are expected.
- **Worker portal tokens**: signed tokens using `WORKER_PORTAL_SECRET`, validated by `WorkerAccessMiddleware` and endpoints in `app/api/worker_auth.py`/`routes_worker.py`.
- **Client portal tokens**: HMAC tokens for invoice/portal links using `CLIENT_PORTAL_SECRET` with TTL (`app/api/routes_payments.py`, `app/settings.py`).
- **Client portal isolation**: client magic links set an org context and are required for every portal call; booking/invoice/photos are resolved only when the authenticated client owns the record and the invoice/order lives under the caller's org. Signed photo downloads stay behind authenticated `/client/orders/{id}/photos/{photo_id}/signed_url` hops and reuse org-aware storage signing.
- **Public endpoints**: `/healthz`, estimator, chat, leads, slots/bookings (with optional captcha), and Stripe webhook; all others require auth.
- **IAM onboarding (temp passwords)**: admins issue org-scoped temp passwords via `/v1/iam/users` or reset endpoints; hashes are stored immediately and never logged. Temp-password users are marked `must_change_password` and are blocked by `PasswordChangeGateMiddleware` from all routes except login/refresh/me/logout/change-password until they set a new password.
- **Unauthorized vs forbidden**: 401 is reserved for missing/invalid credentials or expired sessions; authenticated-but-disallowed calls (role, org, CSRF, consent) return 403.

## Authorization & RBAC
- **Admin roles**: OWNER/ADMIN/DISPATCHER/FINANCE/VIEWER map to permissions in `admin_auth.py` and SaaS membership roles in `saas_auth.py`.
- **SaaS entitlements**: per-plan limits for workers/bookings/storage enforced via dependencies in `app/api/entitlements.py` backed by `app/domain/saas/billing_service.py`.
- **Org scoping**: `request.state.current_org_id` populated by SaaS tokens or `default_org_id`; DB queries filter by `org_id` on every joined table. Admin finance/report/export endpoints (`/v1/admin/reports/*`, `/v1/admin/exports/*`, `/v1/admin/export-dead-letter`) and payments are constrained to the caller's org via `_org_scope_filters` and `resolve_org_id`.
- **Postgres row-level security**: As a safety net, RLS is enabled for org-owned tables (leads, bookings, invoices, invoice_payments, workers, teams, order_photos, export_events, email_events). Each request sets a context var-backed `app.current_org_id` and `SET LOCAL app.current_org_id = '<uuid>'` runs at transaction start for Postgres connections. Queries must continue to include explicit `org_id` filters; RLS blocks cross-org access if a filter is missed. SQLite runs without RLS for local testing.

## Session and token handling
- **Access/refresh**: access JWTs carry `org_id`, `role`, `sid`; refresh tokens rotate sessions and revoke prior ones (`saas_auth.py`, `app/infra/auth.py`).
- **Password hashing**: Argon2id default with bcrypt fallback; legacy SHA-256 hashes auto-upgrade (`app/infra/auth.py`).
- **Revocation**: logout/password change revokes session records; `PasswordChangeGateMiddleware` blocks requests until password updated. Admin-driven temp-password resets/deactivations call per-org session revocation so stale access/refresh tokens cannot be replayed.
- **CSRF**: token helpers in `app/infra/csrf.py` for stateful flows.

## Token TTLs and limits
- **Access token TTL**: `AUTH_ACCESS_TOKEN_TTL_MINUTES` (default 15 minutes) controls JWT validity returned from `/v1/auth/login`/`refresh`.
- **Session TTL**: `AUTH_SESSION_TTL_MINUTES` caps session `expires_at`; refresh tokens rotate sessions before expiry.
- **Refresh token TTL**: `AUTH_REFRESH_TOKEN_TTL_MINUTES` (default 14 days) caps refresh token use; rotation sets `revoked_reason` on prior session.
- **Temp passwords**: one-time secrets returned only in admin IAM responses; optional emailing is controlled by `EMAIL_TEMP_PASSWORDS`. They remain valid only until the user sets a new password, after which all sessions are rotated.

## Transport and rate limits
- **Rate limiting**: middleware using Redis or in-memory limiter with proxy-aware client key resolution (`app/main.py`, `app/infra/security.py`).
- **Admin idempotency**: dangerous admin actions (payments, resends/replays, bulk updates, IAM resets) require an `Idempotency-Key` header. Requests are hashed on method/path/normalized JSON body and persisted per-org in `admin_idempotency`; repeated hashes replay the stored response while mismatched payloads return a 409 Problem+JSON. Per-org action rate limits share the same limiter infrastructure and are applied before execution.
- **CORS**: origins enforced via settings; `STRICT_CORS` recommended for prod (`app/main.py`, `app/settings.py`).
- **Metrics/headers**: security headers middleware sets X-Content-Type-Options, X-Frame-Options, CSP, Referrer-Policy (`app/main.py`). Admin Basic Auth middleware only applies to `/v1/admin/*`; `/healthz`, `/readyz`, and `/metrics` are allowlisted from admin credentials.

## Data access and privacy
- **Photos/files**: access via signed URLs or tokenized download endpoints; TTL and MIME/size limits enforced (`app/api/photo_tokens.py`, `app/infra/storage/backends.py`).
- **Invoices and payments**: invoice tokens HMAC-signed and scoped to invoice ID/org (`app/api/routes_payments.py`).
- **Exports**: outbound webhooks restricted by allowlist and optional HTTPS enforcement (`app/infra/export.py`).
- **PII**: avoid logging PII; request logging uses request_id/paths without body dumps by default. Structured logs automatically redact emails, phone numbers, street addresses, Authorization headers, bearer tokens, and signed URL/query tokens.

## Rate limiting and abuse protection
- Per-client limits default to `RATE_LIMIT_PER_MINUTE`; captcha available for `/v1/leads` when `captcha_mode=turnstile` to deter spam (`app/infra/captcha.py`, `app/api/routes_leads.py`).

## Photo access policy
- Uploads are MIME/size validated and stored under org/order prefixes; minting download links always requires authenticated callers (worker/admin/client) hitting `/photos/{id}/signed_url`. Tokens encode org/order/photo (and optional UA hash or one-time Redis guard), expire after `photo_url_ttl_seconds` (60s default), and are validated by `/signed-download` before redirecting. Responses set `no-store`/`no-cache` headers and never return bare cloud object URLs. Local/S3/R2 use presigned GETs with TTL; Cloudflare Images redirects include signed exp/sig and optional variants (original/thumbnail) to block hotlinking.
