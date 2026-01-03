# Content Guide and Change Rules

## Coding patterns
- **Keep logic in domain services** (`app/domain/**`) and surface via routers; avoid DB writes directly in routers.
- **Typing**: use explicit types and dataclasses/Pydantic models where present; mirror existing function signatures.
- **Logging**: prefer structured logging via `logging.getLogger` with `extra` payloads (see `app/main.py` and `app/infra/logging.py`). Do not log secrets or tokens. Logging is JSON-formatted with automatic redaction of emails, phone numbers, street addresses, bearer/Authorization values, signed URLs, and `token`/`signature` query parameters; always log identifiers (request_id/org_id/user_id/role) instead of payloads.
- **Errors**: raise `DomainError` for business failures; HTTP errors use the shared `problem_details` factory (`app/api/problem_details.py`) so every error includes `type`, `title`, `status`, `detail`, `request_id`, and `errors`.
- **Status codes**: follow existing patterns—422 for validation, 400/409 for business conflicts, 401/403 for auth/permission, 402 for plan limits, 404 for missing resources.
- **No broad refactors**: preserve router/service boundaries and existing public schemas; prefer additive, minimal diffs.

### Problem+JSON conventions
- All error responses must use RFC7807-style payloads via `problem_details`, with `application/problem+json` content type.
- Always include `request_id` (set by middleware or by `problem_details`) and populate `errors` with an empty list when no field-level errors exist.
- Default types are derived from HTTP status (`domain` for 4xx, `server` for 5xx) unless a specific type is provided (e.g., validation, rate limit).

### Auth and conflict status mapping

| Scenario | Status | Notes |
| --- | --- | --- |
| Missing/invalid credentials or session | 401 Unauthorized | Never use for permission/role failures. |
| Authenticated but forbidden (role, org mismatch, CSRF) | 403 Forbidden | Includes cross-org access blocks. |
| Business conflict (already paid/not required, optimistic checks) | 409 Conflict | Use instead of 400 for state conflicts. |
| Rate limiting | 429 Too Many Requests | Always emit Problem+JSON with `rate-limit` type. |

## Security rules
- **Org scoping**: always pass through `require_org_context`, `TenantSessionMiddleware`, or `resolve_org_id`; never trust client-sent org identifiers without validation (`app/api/saas_auth.py`, `app/api/org_context.py`, `app/api/entitlements.py`).
- **Auth**: keep admin Basic Auth checks (`admin_auth.py`) and SaaS JWT validation intact; do not weaken password hashing (`app/infra/auth.py`).
- **Token handling**: never log JWTs, refresh tokens, or photo tokens; store secrets in env vars, not code.
- **Photo/URL safety**: signed photo URLs must remain scoped/time-limited (`app/api/photo_tokens.py`, `app/infra/storage/backends.py`); do not expose raw storage paths.
- **CSV/exports**: ensure CSV outputs are escaped and gated by auth; export webhooks must enforce allowlists (`app/infra/export.py`).
- **Scheduling/bulk ops**: bulk assignment/status/reminder endpoints must stay org-scoped, record admin audits when changing states, and reuse booking service conflict rules/buffers when moving bookings or blocking time.
- **CSV injection prevention**: prefix cells beginning with `= + - @` or tabs to neutralize Excel formula execution and strip/normalize values before writing.
- **Input validation**: keep Turnstile verification when `captcha_mode` is on; validate MIME/size for uploads.

## Testing rules
- Markers: `@pytest.mark.sanity`, `@pytest.mark.smoke`, `@pytest.mark.postgres`, `@pytest.mark.migrations` (`pytest.ini`).
- **How to run**: `make test` (unit + integration), `pytest -m "smoke"` for DB-backed smokes, `pytest -m "migrations"` for Alembic invariants.
- **Adding tests**: co-locate tests under `tests/` mirroring module paths; prefer async tests for async routes/services; use factories/fixtures already in the suite.

## PR and commit conventions
- Use concise, descriptive commit messages; keep PR body summary + tests run.
- Avoid unrelated file churn; do not modify `package.json`/`package-lock.json` per repo rule.
- Document new behavior in the relevant root docs (this set) when adding features.

## DO NOT list
- Do not weaken auth/authorization checks or bypass entitlements.
- Do not add publicly accessible photo URLs or disable signed URL TTLs.
- Do not hardcode secrets, API keys, or Stripe/email credentials.
- Do not remove health/readyz checks or metrics middleware.
- Do not delete retention/cleanup/export/email cron endpoints; schedulers depend on them.
- Do not log Authorization headers, signed URLs, raw tokens (JWTs, refresh, photo, export/webhook), or unredacted emails/phone numbers/addresses.
