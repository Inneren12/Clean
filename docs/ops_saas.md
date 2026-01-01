# SaaS operations guide

Operate the Cleaning Economy API as a production SaaS with reproducible deploys, clear rollback steps, and observability hooks.

## Topology

- **API**: stateless FastAPI service. Horizontal scaling is safe when a shared Redis cache is available for rate limiting (set `REDIS_URL`). Without Redis the in-memory limiter is node-local.
- **Jobs runner**: run `python -m app.jobs.run` as a separate process or container. A heartbeat is written to the `job_heartbeats` table so readiness probes can ensure jobs are alive.
- **Redis**: optional but recommended for distributed rate limiting.
- **Postgres**: primary database. `/readyz` checks connectivity and migration head drift.

### Docker compose layout (reference)

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

## Deploy steps

1. **Build**: `docker build -t registry.example.com/clean/api:$GIT_SHA .` (repeat for jobs runner if separate).
2. **Config**: populate `.env.production` from `.env.production.example` and store in your secret manager; inject at deploy time.
3. **Migrations**: run `alembic upgrade head` against the target database (see rollback below). Prefer running migrations before traffic shift.
4. **Release**:
   - Apply new containers (Kubernetes `Deployment` rollout or Compose `up -d --no-deps api jobs`).
   - Wait for `/readyz` to return 200 (includes DB head + job heartbeat when enabled).
5. **Smoke**: hit `/healthz`, `/readyz`, and run `k6 run scripts/load/saas_k6.js` with a small VU count (see `scripts/load/README.md`).

## Rollback and migrations

- **Schema safety**: keep migrations backward-compatible for one release (no DROP without replace). Use feature flags to gate new columns.
- **Rollback**: redeploy the previous container image and run `alembic downgrade -1` only if the release introduced breaking migrations. Always take a DB snapshot before downgrading.
- **Data backfill jobs**: run via `python -m app.jobs.run --once --job <name>`; re-run after rollback when needed.

## Secrets management

- Store secrets in a managed vault (AWS Secrets Manager, GCP Secret Manager, or Kubernetes Secrets). Never commit credentials.
- Required keys: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `SENDGRID_API_KEY`/`SMTP_PASSWORD`, `SENTRY_DSN`, database credentials, and Redis password (if enabled).
- Rotate secrets by updating the secret store, restarting the deployment, and validating `/readyz`.

## Logging and metrics

- **Logging**: structured JSON logs are emitted to stdout. Aggregate via your log pipeline and alert on spikes in 5xx or Stripe/email errors.
- **Metrics**: set `METRICS_ENABLED=true` to expose Prometheus-format metrics at `/metrics`, including `http_request_latency_seconds`, `http_5xx_total`, `webhook_errors_total`, `email_jobs_total`, `job_runner_up`, and `circuit_state` gauges.
- **Dashboards**: track p95/p99 latency per path, error rates, and DB pool utilization (see `LOAD_METRICS_URL` in load harness for scraping hints).

## Rate limiting

- Default: in-memory limiter with `RATE_LIMIT_PER_MINUTE` and `RATE_LIMIT_CLEANUP_MINUTES`.
- Distributed: set `REDIS_URL` so all nodes share quotas. If Redis is unavailable, requests are allowed to prevent false positives; monitor Redis health and 429 rates.

## Jobs runner expectations

- Run continuously (systemd, container, or cloud task). Use `--interval` to control loop frequency or `--once` for ad-hoc invocations.
- Heartbeats are written each loop iteration to `job_heartbeats` under the name `jobs-runner`.
- Email adapters are resolved at startup; ensure SMTP/SendGrid credentials are configured in the environment.

## Validation checklists

- `/readyz` returns 200 and reports migration head in sync.
- `job_runner_up{job="jobs-runner"}` equals 1 when metrics are enabled.
- k6 smoke load passes thresholds and error rate under 5%.
- Chaos drills in `scripts/chaos/` complete without hangs; DLQ and circuit gauges rise appropriately during failure windows.
