# Jobs runner runbook

The jobs runner is a thin CLI wrapper around the in-app email workflows. It can be scheduled by cron, Cloudflare Workers Cron, or any container task runner without adding new dependencies.

## Commands

```bash
# Run all jobs once
python -m app.jobs.run --once

# Run only booking reminders
python -m app.jobs.run --job booking-reminders --once

# Run invoice and NPS jobs in a loop every 5 minutes with an explicit public base URL
python -m app.jobs.run --job invoice-reminders --job nps-send --interval 300 --base-url "https://app.example.com"
```

Jobs available:

- `booking-reminders`: send 24h reminders for upcoming bookings with an email address.
- `invoice-reminders`: send invoice delivery emails and one-time overdue reminders.
- `nps-send`: send NPS survey links for completed bookings.

Use `--once` for cron-style execution; omit it to keep the process running with the provided `--interval` (defaults to 60 seconds).

## Cron examples

```
# Every 10 minutes: send booking reminders and NPS surveys
*/10 * * * * cd /workspace/Clean && python -m app.jobs.run --job booking-reminders --job nps-send --base-url "https://app.example.com" --once

# Hourly: deliver invoices and overdue nudges
0 * * * * cd /workspace/Clean && python -m app.jobs.run --job invoice-reminders --base-url "https://app.example.com" --once
```

Ensure `PUBLIC_BASE_URL` is set (or pass `--base-url`) so invoice/NPS links are generated correctly. The runner respects the configured email adapter and relies on `email_events` for idempotency, making it safe to re-run.
