# Security Model

## Authentication mechanisms
- **Admin/Dispatcher/Finance/Viewer Basic Auth**: enforced by `AdminAccessMiddleware` with per-role permissions and auditing (`app/api/admin_auth.py`). Credentials come from env vars (owner/admin/dispatcher/accountant/viewer pairs).
- **SaaS JWT (org-scoped)**: access tokens validated by `TenantSessionMiddleware`; sessions stored in DB and refreshed via `/v1/auth/refresh` (`app/api/saas_auth.py`, `app/api/routes_auth.py`). `require_org_context` enforces org presence when SaaS tokens are expected.
- **Worker portal tokens**: signed tokens using `WORKER_PORTAL_SECRET`, validated by `WorkerAccessMiddleware` and endpoints in `app/api/worker_auth.py`/`routes_worker.py`.
- **Client portal tokens**: HMAC tokens for invoice/portal links using `CLIENT_PORTAL_SECRET` with TTL (`app/api/routes_payments.py`, `app/settings.py`).
- **Public endpoints**: `/healthz`, estimator, chat, leads, slots/bookings (with optional captcha), and Stripe webhook; all others require auth.

## Authorization & RBAC
- **Admin roles**: OWNER/ADMIN/DISPATCHER/FINANCE/VIEWER map to permissions in `admin_auth.py` and SaaS membership roles in `saas_auth.py`.
- **SaaS entitlements**: per-plan limits for workers/bookings/storage enforced via dependencies in `app/api/entitlements.py` backed by `app/domain/saas/billing_service.py`.
- **Org scoping**: `request.state.current_org_id` populated by SaaS tokens or `default_org_id`; DB queries should filter by `org_id` where applicable (SaaS users/memberships/billing usage).

## Session and token handling
- **Access/refresh**: access JWTs carry `org_id`, `role`, `sid`; refresh tokens rotate sessions and revoke prior ones (`saas_auth.py`, `app/infra/auth.py`).
- **Password hashing**: Argon2id default with bcrypt fallback; legacy SHA-256 hashes auto-upgrade (`app/infra/auth.py`).
- **Revocation**: logout/password change revokes session records; `PasswordChangeGateMiddleware` blocks requests until password updated.
- **CSRF**: token helpers in `app/infra/csrf.py` for stateful flows.

## Transport and rate limits
- **Rate limiting**: middleware using Redis or in-memory limiter with proxy-aware client key resolution (`app/main.py`, `app/infra/security.py`).
- **CORS**: origins enforced via settings; `STRICT_CORS` recommended for prod (`app/main.py`, `app/settings.py`).
- **Metrics/headers**: security headers middleware sets X-Content-Type-Options, X-Frame-Options, CSP, Referrer-Policy (`app/main.py`).

## Data access and privacy
- **Photos/files**: access via signed URLs or tokenized download endpoints; TTL and MIME/size limits enforced (`app/api/photo_tokens.py`, `app/infra/storage/backends.py`).
- **Invoices and payments**: invoice tokens HMAC-signed and scoped to invoice ID/org (`app/api/routes_payments.py`).
- **Exports**: outbound webhooks restricted by allowlist and optional HTTPS enforcement (`app/infra/export.py`).
- **PII**: avoid logging PII; request logging uses request_id/paths without body dumps by default.

## Rate limiting and abuse protection
- Per-client limits default to `RATE_LIMIT_PER_MINUTE`; captcha available for `/v1/leads` when `captcha_mode=turnstile` to deter spam (`app/infra/captcha.py`, `app/api/routes_leads.py`).

## Photo access policy
- Uploads validated for MIME and size; storage backend determines signed URL behavior. Local/S3/R2 use signed URLs with TTL; Cloudflare Images may use variant-based signed URLs. Do not expose permanent public URLs; honor `photo_url_ttl_seconds` and `order_photo_signed_url_ttl_seconds` settings.
