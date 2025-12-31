# SaaS operations guide

This app can run multiple API instances plus a dedicated background jobs process. Use the guidance below to operate it like a multi-tenant SaaS.

## Processes and topology

- **API**: stateless FastAPI service. Horizontal scaling is safe when a shared Redis cache is available for rate limiting (set `REDIS_URL`). Without Redis the in-memory limiter is node-local.
- **Jobs runner**: run `python -m app.jobs.run` as a separate process or container. A heartbeat is written to the `job_heartbeats` table so readiness probes can ensure jobs are alive.
- **Redis**: optional but recommended for distributed rate limiting.
- **Postgres**: primary database. `/readyz` checks connectivity and migration head drift.

### docker-compose example

A local multi-service layout:

```yaml
services:
  api:
    build: .
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
  jobs:
    build: .
    command: python -m app.jobs.run --interval 60
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
  redis:
    image: redis:7-alpine
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: cleaning
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d cleaning"]
```

## Readiness and alerts

- `/healthz` is lightweight; `/readyz` validates the database and migration heads. When `JOB_HEARTBEAT_REQUIRED=true`, `/readyz` also asserts that the jobs heartbeat is fresh (threshold set by `JOB_HEARTBEAT_TTL_SECONDS`).
- Configure alerting on:
  - Database unreachable or migration drift reported by `/readyz`.
  - Missing or stale job heartbeat.
  - HTTP 5xx spikes using the `http_5xx_total` metric.
  - Webhook failures/ignored events and email job errors via Prometheus counters.

## Metrics

Enable Prometheus metrics with `METRICS_ENABLED=true`. The `/metrics` endpoint exposes:

- `webhook_events_total{result}` – processed/ignored/error counts for Stripe webhooks.
- `email_jobs_total{job,status}` – sent/skipped/error counts per scheduled job type.
- `bookings_total{action}` – booking lifecycle events (created/cancelled).
- `http_5xx_total{method,path}` – server error responses.

## Rate limiting

- Default: in-memory limiter with `RATE_LIMIT_PER_MINUTE` and `RATE_LIMIT_CLEANUP_MINUTES`.
- Distributed: set `REDIS_URL` so all nodes share quotas. If Redis is unavailable, requests are allowed to prevent false positives; monitor Redis health and 429 rates.

## Jobs runner

- Run continuously (e.g., systemd service, container, or Heroku worker). Use `--interval` to control loop frequency or `--once` for ad-hoc invocations.
- Heartbeats are written each loop iteration to `job_heartbeats` under the name `jobs-runner`.
- Email adapters are resolved at startup; ensure SMTP/SendGrid credentials are configured in the environment.
