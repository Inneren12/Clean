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
