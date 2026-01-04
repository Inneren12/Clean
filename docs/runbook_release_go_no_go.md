# Release GO/NO-GO Runbook

This runbook maps to `stage.md` (Release checklist) and `OPERATIONS.md` (Deploy order/jobs). Every step includes a verification command and expected outcome for operators.

## Preconditions
- **Repo and env loaded:** checkout the release branch and ensure `.env` is present.
  - How to verify: `ls .env` — file exists with non-empty size.
- **Prod mode enforced:** `APP_ENV=prod`, `STRICT_CORS=true` with explicit origins, proxy trust lists set if behind a proxy.
  - How to verify: `python - <<'PY'
from app.settings import settings
print({
    "app_env": settings.app_env,
    "strict_cors": settings.strict_cors,
    "cors_origins": settings.cors_origins,
    "trust_proxy_headers": settings.trust_proxy_headers,
    "trusted_proxy_ips": settings.trusted_proxy_ips,
    "trusted_proxy_cidrs": settings.trusted_proxy_cidrs,
})
PY` — expect `app_env`=`"prod"`, `strict_cors`=`True`, trusted proxy lists populated when applicable.

## Config
- **Secrets loaded:** auth/portal/photo/metrics secrets, Basic Auth pairs, Stripe keys, email credentials, storage signing keys.
  - How to verify: `python - <<'PY'
from app.settings import settings
print({
    "auth_secret_key": bool(settings.auth_secret_key and settings.auth_secret_key != "dev-auth-secret"),
    "basic_auth": {
        "owner": settings.owner_basic_username,
        "admin": settings.admin_basic_username,
        "dispatcher": settings.dispatcher_basic_username,
        "accountant": settings.accountant_basic_username,
        "viewer": settings.viewer_basic_username,
    },
    "stripe_secret": bool(settings.stripe_secret_key),
    "email_mode": settings.email_mode,
    "storage_backend": settings.order_storage_backend,
    "metrics_token": bool(getattr(settings, "metrics_token", None)),
})
PY` — expect `True` for secret booleans and non-null Basic Auth usernames for required roles.
- **CORS and proxy trust applied:** `STRICT_CORS=true` with `CORS_ORIGINS` set; `TRUST_PROXY_HEADERS=true` plus `TRUSTED_PROXY_IPS/CIDRS` when behind a load balancer.
  - How to verify: same settings dump above shows `strict_cors` true, `cors_origins` non-empty, and proxy trust values populated if used.
- **Admin safety flags known:** `ADMIN_READ_ONLY` off for normal ops; `ADMIN_MFA_REQUIRED` enabled for OWNER/ADMIN SaaS users when mandated.
  - How to verify: `python - <<'PY'
from app.settings import settings
print({"admin_read_only": settings.admin_read_only, "admin_mfa_required": settings.admin_mfa_required})
PY` — expect `admin_read_only=False` unless intentionally in incident mode, and `admin_mfa_required` per policy.

## Migrations
- **Apply migrations:** `make migrate` inside the running API container/environment.
  - Expected: command exits 0.
- **Single Alembic head and DB at head:**
  - How to verify: `alembic heads` — returns one revision hash; `alembic current` — matches the same hash.
  - `/readyz` must report `database.migrations_current=true` (see Health section).
- **RLS policies present:** ensure Postgres policies from `0044_postgres_rls_org_isolation` are applied.
  - How to verify: `psql "$DATABASE_URL" -c "SELECT polname FROM pg_policies WHERE polname LIKE '%org_isolation%'"` — expect multiple policy rows.
  - How to verify isolation: `psql "$DATABASE_URL" -c "SET LOCAL app.current_org_id = '<org_uuid>'; SELECT COUNT(*) FROM leads;"` — returns only the tenant’s rows; same query without `SET LOCAL` should return 0.

## Jobs / Scheduler
- **Cron/Cloudflare Scheduler endpoints (Basic Auth):**
  - Cleanup pending bookings: `curl -u "$ADMIN_BASIC_USERNAME:$ADMIN_BASIC_PASSWORD" -X POST "$API_BASE/v1/admin/cleanup"` — expect `202` with `{ "deleted": <int> }`.
  - Email scan/reminders: `curl -u "$ADMIN_BASIC_USERNAME:$ADMIN_BASIC_PASSWORD" -X POST "$API_BASE/v1/admin/email-scan"` — expect `202` with counts.
  - Retention cleanup: `curl -u "$ADMIN_BASIC_USERNAME:$ADMIN_BASIC_PASSWORD" -X POST "$API_BASE/v1/admin/retention/cleanup"` — expect `200` with `{ "deleted": <int> }`.
  - Export DLQ replay surface: `curl -u "$ADMIN_BASIC_USERNAME:$ADMIN_BASIC_PASSWORD" "$API_BASE/v1/admin/export-dead-letter?limit=1"` — expect `200` JSON array (empty or queued rows).
  - Outbox DLQ surface: `curl -u "$ADMIN_BASIC_USERNAME:$ADMIN_BASIC_PASSWORD" "$API_BASE/v1/admin/outbox/dead-letter?limit=1"` — expect `200` JSON array.
- **Background loop (optional):** run `python -m app.jobs.run --job outbox-delivery --job storage-janitor --job email-dlq --once` where the API environment variables are available.
  - Expected: process logs show `job_complete` and exit 0.
- **Job heartbeat monitoring:** when `JOB_HEARTBEAT_REQUIRED=true`, ensure the `jobs-runner` heartbeat is fresh.
  - How to verify: `curl "$API_BASE/readyz" | jq '.jobs'` — expect `{"ok":true,"enabled":true,...}` with `age_seconds` below `JOB_HEARTBEAT_TTL_SECONDS`.
  - Operator view: `curl -u "$ADMIN_BASIC_USERNAME:$ADMIN_BASIC_PASSWORD" "$API_BASE/v1/admin/jobs/status"` — expect recent `last_success_at` and low `consecutive_failures` for each job.

## Health
- **Liveness:** `curl "$API_BASE/healthz"` — expect `200` and `{ "status": "ok" }` (no auth required).
- **Readiness:** `curl "$API_BASE/readyz"` — expect `200` with `status="ok"`, `database.ok=true`, `database.migrations_current=true`, `jobs.ok=true` (or `enabled=false` if heartbeat check disabled). Returns `503` on failures.

## Smoke tests
- **Estimator:** `curl -X POST "$API_BASE/v1/estimate" -H "Content-Type: application/json" -d '{"beds":2,"baths":1.5,"cleaning_type":"deep"}'` — expect `200` with `pricing_config_id` and `total_before_tax` fields.
- **Lead intake (when captcha enabled):** `curl -X POST "$API_BASE/v1/leads" -H "Content-Type: application/json" -d '{"name":"Smoke Lead","phone":"780-555-0000","email":"lead@example.com","postal_code":"T5J0N3","structured_inputs":{"beds":1,"baths":1,"cleaning_type":"standard"}}'` — expect `201` with a `lead_id`; captcha must be satisfied when `CAPTCHA_MODE!=off`.
- **Admin metrics CSV:** `curl -u "$ADMIN_BASIC_USERNAME:$ADMIN_BASIC_PASSWORD" "$API_BASE/v1/admin/metrics?format=csv" | head -n 1` — expect CSV header row (e.g., `metric,from,to,value`).
- **Queues sanity:** `curl -u "$DISPATCHER_BASIC_USERNAME:$DISPATCHER_BASIC_PASSWORD" "$API_BASE/v1/admin/queue/photos?status=pending&limit=1"` — expect `200` JSON array (possibly empty). Use finance creds for `/v1/admin/queue/invoices` if required.

## Backups / Restore
- **Database backup:** `./scripts/backup_pg.sh` — expect creation of a `.dump` file in the current directory; command exits 0.
- **Database restore drill:** `ALLOW_CREATE_IN_DUMP=1 ./scripts/restore_pg.sh <dump_file>` when restoring a dump created with `--create`; otherwise omit the flag. Expect exit 0 and `alembic current` matching `alembic heads` afterward.
- **Storage:** verify object storage or `ORDER_UPLOAD_ROOT` volume is snapshotted per infrastructure policy.

## Security switches
- **Incident read-only:** if `ADMIN_READ_ONLY=true`, POST/PUT/PATCH/DELETE on `/v1/admin/*` and `/v1/iam/*` should return `409` Problem+JSON. Disable after incident.
  - How to verify: `curl -u "$ADMIN_BASIC_USERNAME:$ADMIN_BASIC_PASSWORD" -X POST "$API_BASE/v1/admin/email-scan"` — expect `409` when read-only is enabled; `202` when disabled.
- **Break-glass token:** during read-only, start a session with `curl -u "$OWNER_BASIC_USERNAME:$OWNER_BASIC_PASSWORD" -X POST "$API_BASE/v1/admin/break-glass/start" -H "Content-Type: application/json" -d '{"reason":"incident","ttl_minutes":30}'` — expect token payload; use `X-Break-Glass-Token` on allowed writes.
- **Admin MFA:** ensure `ADMIN_MFA_REQUIRED=true` in production for SaaS OWNER/ADMIN. Test login flows separately if toggled.

## Rollback
- **Application rollback:** redeploy the prior application image/container and restart services.
  - How to verify: `curl "$API_BASE/readyz"` — expect `status="ok"` after rollback.
- **Database rollback (preferred via restore):** restore the most recent backup (`restore_pg.sh`) instead of downgrading migrations when possible. Confirm `alembic current` matches the intended revision and rerun `/readyz` to ensure migrations and heartbeats are green.
