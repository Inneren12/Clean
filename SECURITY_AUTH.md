# Authentication Hardening

This document summarizes the authentication changes shipped in Sprint 11.

## Password hashing
- **Default**: `argon2id` using configurable time, memory, and parallelism costs.
- **Fallback**: `bcrypt` with a configurable cost factor.
- **Legacy support**: SHA-256 (`salt$digest`) hashes continue to verify. When a legacy hash verifies successfully it is transparently re-hashed using the current scheme and persisted.
- Hash strings are versioned with prefixes (`argon2id$`, `bcrypt$`, `sha256$`) so verification can dispatch safely.

### Configuration
- `PASSWORD_HASH_SCHEME` — `argon2id` (default) or `bcrypt`.
- `PASSWORD_HASH_ARGON2_TIME_COST`
- `PASSWORD_HASH_ARGON2_MEMORY_COST`
- `PASSWORD_HASH_ARGON2_PARALLELISM`
- `PASSWORD_HASH_BCRYPT_COST`

## Token and session lifecycle
- Login issues a short-lived **access token** (JWT) carrying a `sid` (session id) and `jti` plus a long-lived opaque **refresh token** (hashed server-side).
- Sessions are recorded in `saas_sessions` with explicit `expires_at`, `refresh_expires_at`, and revocation metadata.
- Refresh requests rotate sessions atomically: a new session + refresh token are minted and the prior session is revoked.
- Logout revokes the active session immediately.
- Access checks enforce session expiry/revocation on every request; expired or revoked sessions result in `401` even if the JWT is not expired.

### Configuration
- `AUTH_ACCESS_TOKEN_TTL_MINUTES` — default 15 minutes.
- `AUTH_REFRESH_TOKEN_TTL_MINUTES` — default 14 days.
- `AUTH_SESSION_TTL_MINUTES` — default 24 hours.
- `SESSION_TTL_MINUTES_WORKER` / `SESSION_TTL_MINUTES_CLIENT` — worker/client portal session TTLs.
- `SESSION_ROTATION_GRACE_MINUTES` — grace window for rotation (server default 5 minutes; rotations are immediate in current implementation).

## Audit
- Token lifecycle events (`issued`, `refreshed`, `revoked`) are written to `token_events` with user/org identifiers, session id, actor role, timestamps, request id (if available), and free-form metadata.

## Revocation
- Sessions can be revoked individually (logout) or in bulk per user (password reset workflows can call `revoke_user_sessions`).
- Refresh tokens are rotated; previous refresh tokens are invalidated immediately.

## Worker portal sessions
- Worker sessions are HMAC-signed and now embed an expiry timestamp derived from `SESSION_TTL_MINUTES_WORKER`. Legacy tokens without expiry will be rejected, prompting a re-login.

## Client portal token scope/TTL
- Client portal magic links are HMAC tokens signed with `CLIENT_PORTAL_SECRET` and include `email`, `client_id`, `org_id`, `iat`, and `exp` claims. Tokens are valid only for the organization that issued them and set the org context on every request.
- TTL is controlled by `CLIENT_PORTAL_TOKEN_TTL_MINUTES`; expired, cross-org, or tampered tokens return Problem+JSON `401/403` responses and do not leak token contents.
- Token transport must use the `client_session` HTTP-only cookie (set by `/client/login/callback`) or `Authorization: Bearer` headers; avoid logging tokens and rotate the secret on suspicion of compromise.

## Legacy Basic Auth
- Legacy admin Basic Auth remains supported for backward compatibility but is **deprecated**. Prefer SaaS identities with MFA enforced (`ADMIN_MFA_REQUIRED=true`) for all operators.
- In production (`APP_ENV=prod`), legacy Basic Auth is disabled by default; operators must explicitly set `LEGACY_BASIC_AUTH_ENABLED=true` to allow it. This reduces the attack surface when environment configuration is missing or typoed.
- For development and automated tests (`APP_ENV=dev` or `settings.testing=true`), Basic Auth can remain on for convenience while SaaS auth is wired.
- Billing pause/resume endpoints (`/v1/billing/pause`, `/v1/billing/resume`) require SaaS OWNER/ADMIN/FINANCE roles; actions are org-scoped and capture reason codes with pause/resume timestamps.

## Analytics endpoint roles and data safety
- `/v1/admin/analytics/funnel`, `/v1/admin/analytics/nps`, and `/v1/admin/analytics/cohorts` require FINANCE permission (OWNER/ADMIN/FINANCE roles qualify). Dispatcher/viewer roles cannot access these endpoints.
- All analytics responses are org-scoped and return aggregates only. No raw leads, client identifiers, NPS comments, or booking details are emitted.
- Time-range filters are supported for queries; callers should supply `from`/`to` timestamps to bound aggregation windows and keep workloads predictable.
