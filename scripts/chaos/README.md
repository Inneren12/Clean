# Chaos drills

These scripts inject controlled dependency failures against the docker-compose stack. They are deterministic (no random killing) and rely on short timeouts to prevent hangs.

## Prerequisites

- `docker compose` running the local stack (api, jobs, redis).
- `TARGET_HOST` exported (defaults to `http://localhost:8000`).
- Optional: `DISPATCH_BASIC` when `/v1/admin/email-scan` requires basic auth.

## Scenarios

### Redis down

```
./scripts/chaos/redis_down.sh
```

- Stops the Redis container, verifies `/healthz` still responds within 5s, and posts a lead to confirm rate-limit fallback does not hang requests.
- Restarts Redis and checks `PING` before exiting.

### S3 degraded (latency/drop)

```
CHAOS_S3_HOST=s3.amazonaws.com ./scripts/chaos/s3_degraded.sh
```

- Adds a blackhole route to the S3 host inside the API container to simulate timeouts.
- Verifies connectivity to S3 fails quickly (socket timeout) while `/healthz` remains responsive.

### Email provider down

```
DISPATCH_BASIC=$(echo -n "dispatcher:password" | base64 -w0) \
CHAOS_EMAIL_HOST=smtp.sendgrid.net \
./scripts/chaos/email_down.sh
```

- Blackholes SMTP host inside the jobs container.
- Triggers `POST /v1/admin/email-scan` to push email delivery into the failure path and asserts the call does not hang.
- Confirms SMTP sockets fail fast (timeout) before cleaning up routes.

## Expected behavior

- Controlled failure, no client hangs (`curl` uses 5s timeouts).
- Circuit breakers/metrics should reflect the outage when Prometheus metrics are enabled (e.g., increased `webhook_errors_total`, `email_jobs_total{status="error"}`, or circuit-state gauges).
- Jobs that cannot deliver should land in the DLQ for later replay.
