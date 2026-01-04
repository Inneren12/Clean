# P1 Monitoring Runbook

## What to watch
- `/readyz` probe for DB connectivity, migrations, and jobs heartbeat.
- Background jobs: `job_last_heartbeat_timestamp` and `job_last_success_timestamp` gauges per job.
- Outbox delivery: `outbox_queue_messages{status}` for pending/retry depth and DLQ (`status="dead"`).
- Email sending: `email_adapter_outcomes_total{status}` and `email_dlq_messages{status}` for retries/dead letters.
- Stripe webhooks: `stripe_webhook_events_total{outcome}` and `stripe_webhook_circuit_open_total`.
- Circuit breakers: `circuit_state{circuit}` (Stripe/email).

## Thresholds
- `/readyz` should return 200; any 5xx for 5+ minutes triggers `ReadyzFailures`.
- `job_last_heartbeat_timestamp{job="jobs-runner"}` should be newer than 5 minutes; `job_last_success_timestamp` should advance at least every 15 minutes per job.
- `outbox_queue_messages{status="dead"}` or `email_dlq_messages{status="dead"}` increasing over 15 minutes indicates DLQ growth.
- Stripe webhook error rate above 10% over 10 minutes, or `stripe_webhook_circuit_open_total` increasing, warrants investigation.

## What to do
- **Ready/health issues:** Hit `/readyz` and `/healthz` locally; check database connectivity and recent migrations. Verify `job_last_heartbeat_timestamp` in `/v1/admin/jobs/status`.
- **Job stale or failing:** Inspect logs for the job name, restart the worker, and rerun via `python -m app.jobs.run --job <name> --once`. Confirm gauges update after a run.
- **Outbox DLQ growth:** Query `/v1/admin/outbox/dead-letter` to review failed events. Fix upstream email/webhook/export errors, then replay via `/v1/admin/outbox/{event_id}/replay`.
- **Email delivery errors:** Check adapter credentials; review `email_adapter_outcomes_total{status!="sent"}` and `email_dlq_messages`. After fixes, trigger `email-dlq` job and confirm DLQ drops.
- **Stripe webhook errors:** Check `stripe_webhook_events_total{outcome="error"}` and logs for signature or processing errors. Verify webhook secrets in settings and the Stripe dashboard. If the circuit breaker is open, wait for Stripe recovery and retry.

## Useful endpoints
- `/readyz`, `/healthz`
- `/metrics` (token-gated in prod)
- `/v1/admin/jobs/status`
- `/v1/admin/outbox/dead-letter`
- `/v1/admin/queues/dead-letter`
