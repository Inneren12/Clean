# Incident Runbooks

Actionable procedures for the most common monitoring alerts on the Clean service.

## ServiceNotReady
- **What to check:**
  - `GET /readyz` should return `status: ok` with `database.ok` and `jobs.ok` true; alert is driven by `http_5xx_total{path="/readyz"}`.
  - Inspect recent deploys or restarts around the alert start time.
  - Confirm backing services (Postgres, storage) are reachable from the API pod/VM.
- **What to do:**
  - Roll back or redeploy if a bad build is suspected.
  - Restart the API container/process to clear hung workers.
  - If DB connectivity is failing, validate credentials, security groups, and pool sizing.
- **How to confirm fixed:**
  - `/readyz` returns HTTP 200 with `ok` statuses and `probe_success` returns to 1.
  - Error counters (`http_5xx_total`) stop increasing for normal traffic.

## HighErrorRate
- **What to check:**
  - Identify the top offending paths via `http_5xx_total{path}` and correlate with latency buckets.
  - Review application logs for stack traces matching the paths and timestamps.
  - Verify upstream dependencies (Stripe, email provider, database) are healthy.
- **What to do:**
  - Roll back the last deployment if errors coincide with a release.
  - Mitigate hot paths by enabling circuit breakers or feature flags if available.
  - If dependency failures, fail fast and retry with backoff; coordinate with the dependency owner.
- **How to confirm fixed:**
  - 5xx rate drops below the alert threshold for 2+ evaluation periods.
  - Latency histograms return to baseline and user requests succeed in smoke tests.

## JobsRunnerStale
- **What to check:**
  - `job_last_heartbeat_timestamp{job="jobs-runner"}` age compared to `job_heartbeat_ttl_seconds`.
  - Logs for the jobs-runner container/service to see crashes or stuck loops.
  - `job_last_success_timestamp` for critical jobs (booking reminders, invoice reminders, DLQ, NPS, storage-janitor).
- **What to do:**
  - Restart the jobs runner process or container and watch the heartbeat metric.
  - Re-run failed jobs manually with `python -m app.jobs.run --job <name> --once` if safe. Check `/v1/admin/jobs/status` for `consecutive_failures` and `last_error` details.
  - If the runner cannot connect to the database, verify `DATABASE_URL` and network reachability.
- **How to confirm fixed:**
  - Heartbeat timestamp updates and `job_runner_up{job="jobs-runner"}` stays at 1 for 10+ minutes.
  - Job success timestamps advance after the restart with no new error log lines.

## JobLoopFailures
- **What to check:**
  - Alert is driven by `job_errors_total`; inspect `/v1/admin/jobs/status` for jobs with non-zero `consecutive_failures` and populated `last_error`.
  - Confirm whether the failing job is tied to email sending, DLQ replay, or storage cleanup.
- **What to do:**
  - Fix the underlying dependency (email provider, webhook target) and then rerun the specific job with `python -m app.jobs.run --job <name> --once`.
  - If exports failed, replay with `POST /v1/admin/export-dead-letter/{id}/replay` once the target is healthy.
- **How to confirm fixed:**
  - `job_errors_total` stops incrementing and `job_last_success_timestamp{job}` updates.
  - `/v1/admin/jobs/status` shows `consecutive_failures` reset to 0.

## DBPoolExhaustion
- **What to check:**
  - `increase(db_pool_timeout_total[5m])` values and corresponding application log warnings `db_pool_timeout`.
  - Database CPU/IO utilization and slow query logs for long-running statements.
  - Connection counts vs configured `DATABASE_POOL_SIZE`/`DATABASE_MAX_OVERFLOW`.
- **What to do:**
  - Capture the slow queries and add appropriate indexes or tune statements.
  - Ensure connections are closed: look for hanging transactions or tasks that never release sessions.
  - Temporarily raise `DATABASE_POOL_TIMEOUT_SECONDS` only if queries are legitimately slower during load tests.
- **How to confirm fixed:**
  - `db_pool_timeout_total` stops increasing and application latency stabilizes.
  - Connection count hovers within pool limits under expected load.

## StripeWebhookErrors
- **What to check:**
  - `webhook_errors_total{type}` increases, especially `invalid_signature`, `payload_mismatch`, or `processing_error`.
  - Application logs for webhook handler exceptions; confirm recent Stripe secret rotations.
  - Stripe dashboard for recent delivery attempts and retries.
- **What to do:**
  - Verify the Stripe signing secret and endpoint URL match the deployed environment.
  - Fix code or configuration causing processing failures, then replay events from the Stripe dashboard.
  - If payload shape changes, update validation to accept new fields while keeping signature verification strict.
- **How to confirm fixed:**
  - New webhook deliveries succeed and `webhook_errors_total` plateaus.
  - Stripe shows green checks for recent events with no automatic retries pending.

## EmailJobFailures
- **What to check:**
  - `email_jobs_total{status="error"}` increases and associated job names (booking-reminders, invoice-reminders, nps-send, email-dlq, storage-janitor).
  - SMTP/SendGrid credentials, network egress, and template rendering errors in logs.
  - `email_dlq_messages{status}` growth indicating retries are exhausted; alert uses `email_dlq_messages{status="dead"}`.
- **What to do:**
  - Fix credentials or networking issues; re-run the affected job with `--once` after remediation.
  - Clear DLQ entries by replaying once the root cause is fixed with `POST /v1/admin/export-dead-letter/{id}/replay`; avoid deleting without processing.
  - If templates fail, patch the template or input data and deploy a fix before rerunning jobs.
- **How to confirm fixed:**
  - `email_jobs_total{status="error"}` stops increasing and subsequent runs report `sent` or `skipped` outcomes.
  - DLQ depth declines to zero and users receive backlogged messages.
