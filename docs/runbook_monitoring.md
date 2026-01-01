# Monitoring & Alerting Runbook

This service exposes Prometheus metrics at `/metrics` (token-protected in production) and health details at `/readyz`.

## Key signals
- **HTTP 5xx and latency**: `http_5xx_total` and `http_request_latency_seconds` (labels: `method`, `path`, `status_class`). High error rates or long latencies indicate API regressions or dependency issues.
- **Webhooks**: `webhook_errors_total{type}` and `webhook_events_total{result}` track Stripe webhook processing. Alerts trigger on sustained `invalid_signature`, `payload_mismatch`, or `processing_error` spikes.
- **Email delivery**: `email_notifications_total{template,status}` (statuses: `delivered`, `skipped`, `failed`, `dead`) plus DLQ gauges `email_dlq_messages{status}` to track pending vs. dead letters.
- **Job runner heartbeat**: `job_last_heartbeat_timestamp{job}` and `job_last_success_timestamp{job}` provide liveness. `/readyz` includes a `jobs` stanza with age seconds when `JOB_HEARTBEAT_REQUIRED=true`.
- **Circuit breakers**: `circuit_state{circuit}` exposes breaker state (`0=closed`, `1=open`). Watch for stuck-open breakers on email or payment providers.

## First response checklist
1. **Confirm health endpoints**
   - `GET /readyz` should return `status: ok` with `database.ok=true` and `jobs.ok=true`.
2. **Inspect recent deployments**
   - Check deploy logs for errors aligned with alert start time.
3. **Drill into metrics**
   - 5xx/latency: filter `http_request_latency_seconds`/`http_5xx_total` by `path` to isolate endpoints.
   - Webhooks: look at `webhook_errors_total` by `type` to distinguish bad signatures vs. processing bugs.
   - Emails: compare `email_notifications_total{status="failed"}` with `email_dlq_messages`. Rising `dead` counts mean retries are exhausted.
   - Jobs: verify `job_last_heartbeat_timestamp{job="jobs-runner"}` freshness matches `job_heartbeat_ttl_seconds`.
4. **Common fixes**
   - 5xx spike: roll back recent change, inspect app logs for stack traces with the matching `path`.
   - Webhook signature errors: validate Stripe secret rotation and endpoint configuration; replay recent events from Stripe dashboard if safe.
   - Email failures: confirm SMTP/SendGrid credentials and network connectivity; clear DLQ by fixing credentials then rerunning `email-dlq` job.
   - Job heartbeat stale: restart the jobs runner process or container; check scheduler logs for crashes.

## When escalating
- Escalate to the on-call backend engineer if 5xx alerts persist beyond 15 minutes or DLQ `dead` counts grow after credential fixes.
- Provide recent logs, alert timestamps, and the specific metric slices (labels used) reviewed.
