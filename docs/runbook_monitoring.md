# Monitoring and operational signals

Minimum production monitoring should cover availability, async jobs, and storage pressure. Configure alerts where possible and ensure dashboards are scoped to the `/v1/admin` surface (protected by Cloudflare Access or an equivalent access proxy) and public endpoints separately.

## HTTP/API signals

- **5xx rate**: alert if rolling 5-minute error rate exceeds 2% or any sudden spike. Break down by path to isolate `/v1/admin` vs public APIs.
- **Latency**: watch P95/99 latency for `/healthz` (fast) versus `/readyz` (includes database checks).
- **Rate-limit denials**: elevated 429s can indicate abuse or misconfiguration.

## Webhooks/export

- **Webhook failures**: monitor dead-letter queue growth (`export_events_dead_letter`) and repeated webhook retry errors in logs.
- **Blocked destinations**: alert on `export_webhook_blocked` or `export_webhook_failed` log events.
  - Ensure `EXPORT_WEBHOOK_ALLOWED_HOSTS` is configured as bare hostnames (no scheme), either comma-separated or JSON list, so valid webhook URLs are not accidentally blocked.
- **Billing webhooks**: watch for `stripe_webhook_error` and `stripe_webhook_replayed_mismatch` entries. Subscription events should flip `organization_billing.status` away from `inactive`; alert if status remains `inactive` for paying orgs or if repeated webhook retries are ignored.

## Email and scheduled jobs

- **Email job failures**: alert on consecutive failures in `email_events` processing (invoice reminders, booking reminders, NPS send). Track retries and throttle when provider limits are hit.
- **Job loop health**: heartbeat for `python -m app.jobs.run` processes; alert if missing for 10 minutes.

## Database and storage

- **Disk usage**: track database volume usage and the uploads mount (`ORDER_UPLOAD_ROOT`). Alert at 70%/85% thresholds.
- **Connection errors**: monitor database connection pool errors and migration drift (see `/readyz`).
- **Backup freshness**: alert if no successful `pg_dump` within 24 hours or uploads sync fails.

## Access control

- Place `/v1/admin` behind Cloudflare Access (or another zero-trust gateway) to require SSO/MFA before reaching FastAPI. Keep an allowlist for monitoring probes hitting `/healthz` and `/readyz`.
