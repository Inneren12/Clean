# Incident playbook — Email DLQ growth

## Signal

- `email_dlq_messages` gauge rising; alerts on DLQ > threshold.
- Customers report missing reminders.

## Containment

1. Run `./scripts/chaos/email_down.sh` in staging to validate behavior and timeouts.
2. Pause reminder scans if provider is down (`EMAIL_MODE=off` or disable cron trigger).

## Mitigation

- Fix SMTP/SendGrid credentials or network reachability.
- Drain DLQ by re-running jobs: `python -m app.jobs.run --once --job send_email_reminders` (set provider back to healthy first).
- For repeated failures, switch to alternate provider credentials temporarily.

## Verification

- `email_jobs_total{status="sent"}` increases; DLQ gauge drops to zero.
- Customers receive resend confirmation (test address first).
- k6 smoke still meets latency/error SLOs while DLQ drains.
