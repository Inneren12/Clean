# Release Gate Bot (S4-B)

Use this sheet before promoting a bot build. Keep the commands reproducible and note every manual check.

## Snapshot
- Release SHA: `TODO`
- Bot run link/ID: `TODO`
- Overall result: `TODO (pass/fail)`

## Required commands
- **Lint:** Not configured today. If a linter is added, record the command and result here.
- **Backend tests:**
  ```bash
  python -m pip install --upgrade pip
  pip install -r requirements.txt
  alembic upgrade head
  pytest
  ```
- **Frontend build + unit test:**
  ```bash
  cd web
  npm ci
  npm run build
  npm run test
  ```
- **Typecheck:** Covered by `npm run build` (Next.js TypeScript). No separate command.
- **E2E:** None automated. If a scenario requires end-to-end coverage, run the relevant QA doc (e.g., `docs/qa_s2_e2e.md`) and paste the run link/result.

Record each command’s status next to it (pass/fail + log link when available).

## Manual checklist
- Secrets loaded: `DATABASE_URL`, `ADMIN_BASIC_*`, `DISPATCHER_BASIC_*`, `PRICING_CONFIG_PATH`, `STRICT_CORS=true` with matching `CORS_ORIGINS`, Stripe/email/export keys when those features are on.
- Migrations applied (`alembic upgrade head`) and DB schema matches current models.
- Smoke auth/CORS checks:
  - Admin leads: `curl -i -u "$ADMIN_BASIC_USERNAME:$ADMIN_BASIC_PASSWORD" "${API_BASE:-http://localhost:8000}/v1/admin/leads"`
  - Dispatcher restriction (expect 403): `curl -i -u "$DISPATCHER_BASIC_USERNAME:$DISPATCHER_BASIC_PASSWORD" "${API_BASE:-http://localhost:8000}/v1/admin/metrics"`
  - CORS preflight (staging/prod): `curl -i -X OPTIONS "${API_BASE}/v1/estimate" -H "Origin: ${PAGES_ORIGIN}" -H "Access-Control-Request-Method: POST"`
- Feature flags match the release notes (email/export/referrals/deposits).
- Rollback is ready (previous image tag + env snapshot saved).

## FAQ
- **What counts as a pass?** Every required command runs cleanly, manual checks are signed off, and any skipped step is documented with a reason and approver.
- **Where do I store evidence?** Link CI jobs or local logs next to each command and curl check; keep the latest SHA and bot run ID in the snapshot above.
- **Who can approve an exception?** A product owner or on-call engineer who can accept the documented risk; note their name/time beside the skipped item.
- **How often is this updated?** On every release train; edits belong in this doc so the bot and humans stay aligned.

## Limits
- Does not provision secrets or infra; it assumes environments already exist.
- No automated rollback; you must keep the prior deploy artifact handy.
- Catches regressions covered by the commands above; it cannot validate new manual flows you do not list.

## Included vs. excluded
- **Included:** Backend pytest suite, DB migrations, web build + unit test, TypeScript compile, auth/CORS smoke curls, feature-flag validation, rollback readiness.
- **Excluded:** New API contract testing beyond current suites, third-party SLA checks, data backfill scripts, and live customer confirmations.

## Guarantee wording
- Passing this gate guarantees parity with current CI coverage and smoke checks. It does **not** guarantee zero defects; it confirms we ran the agreed safety net for this release.

## Handoff templates
- Need human: "Flagging for human review—bot is blocked; see notes in release_gate_bot.md."
- Waiting contact: "Paused—awaiting customer contact/credentials. Will resume once received."
- Complaint: "Received complaint; routing to human support. Bot stopped until a human resolves the issue."
- Special case: "Special-case scenario detected. Handing off to human owner; bot steps are on hold."
