# Scheduler Runbook

Use this runbook to configure recurring maintenance calls (cron or Cloudflare Scheduler) and to monitor job heartbeats. All admin endpoints are Basic Auth protected; do **not** expose them publicly without IP allowlists and strong credentials.

## Prerequisites
- Environment variables exported for convenience:
  - `API_BASE=https://api.example.com`
  - `ADMIN_BASIC_USERNAME` / `ADMIN_BASIC_PASSWORD`
- Network path to the API restricted to trusted cron/Scheduler runners.
- `JOB_HEARTBEAT_REQUIRED=true` in production so `/readyz` fails when job heartbeats go stale.

## Required scheduled actions
Use POST requests to the admin endpoints below. Frequencies are safe defaults; adjust only if downstream capacity and queues are healthy.

| Action | Endpoint | Recommended frequency | Rationale | Example |
| --- | --- | --- | --- | --- |
| Cleanup orphaned data and reconcile straggler jobs | `/v1/admin/cleanup` | Daily at 04:00 UTC | Runs heavier maintenance; off-peak timing avoids contention with daytime traffic. | `curl -u "$ADMIN_BASIC_USERNAME:$ADMIN_BASIC_PASSWORD" -X POST "$API_BASE/v1/admin/cleanup"` |
| Email inbound scan | `/v1/admin/email-scan` | Every 5 minutes | Keeps inbox ingestion responsive; prevents large batches from piling up. | `curl -u "$ADMIN_BASIC_USERNAME:$ADMIN_BASIC_PASSWORD" -X POST "$API_BASE/v1/admin/email-scan"` |
| Data retention cleanup | `/v1/admin/retention/cleanup` | Daily at 02:00 UTC | Applies retention policies without clashing with the heavier cleanup task. | `curl -u "$ADMIN_BASIC_USERNAME:$ADMIN_BASIC_PASSWORD" -X POST "$API_BASE/v1/admin/retention/cleanup"` |
| Export DLQ replay | `/v1/admin/export-dead-letter` | Every 15 minutes | Replays failed exports promptly while giving time for upstream fixes. | `curl -u "$ADMIN_BASIC_USERNAME:$ADMIN_BASIC_PASSWORD" -X POST "$API_BASE/v1/admin/export-dead-letter"` |
| Outbox DLQ replay | `/v1/admin/outbox/dead-letter` | Every 15 minutes | Keeps notification/webhook dead letters drained; align with export replay cadence. | `curl -u "$ADMIN_BASIC_USERNAME:$ADMIN_BASIC_PASSWORD" -X POST "$API_BASE/v1/admin/outbox/dead-letter"` |

## Optional jobs via the runner module
Use the built-in runner for jobs that need process-local execution or to backfill during incidents.

```bash
# Storage janitor: removes orphaned files; run daily during a quiet window
python -m app.jobs.run --job storage_janitor --once

# Outbox delivery loop: for environments without an external scheduler
python -m app.jobs.run --job outbox-delivery --once
```

Add `--interval-seconds <n>` if you want the runner to loop instead of a single execution. Keep the same frequencies as above when wiring a long-running process.

## Monitoring and heartbeats
- Status endpoint: `curl -u "$ADMIN_BASIC_USERNAME:$ADMIN_BASIC_PASSWORD" "$API_BASE/v1/admin/jobs/status"`
- Fields per job:
  - `name`: job identifier (e.g., `email-dlq`, `jobs-runner`).
  - `last_heartbeat`: last heartbeat timestamp recorded by the runner.
  - `last_success_at`: last successful completion time.
  - `consecutive_failures`: non-zero when recent attempts failed; should return to 0 after a success.
  - `last_error` / `last_error_at`: populated when failures occur; clear once the job succeeds again.
- Interpretation:
  - Heartbeat older than `JOB_HEARTBEAT_TTL_SECONDS` → restart the scheduler/runner pod or VM.
  - Rising `consecutive_failures` with `last_error` set → investigate the specific job (e.g., provider outage); pause retries if causing load.
  - `/readyz` will return 503 when heartbeats are stale and `JOB_HEARTBEAT_REQUIRED=true`, helping alerting catch broken schedulers.
