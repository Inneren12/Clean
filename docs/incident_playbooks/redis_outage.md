# Incident playbook — Redis outage

## Signal

- Elevated 429s or cache misses; Redis health checks failing.
- `/readyz` still 200 (rate-limit fallback allows requests).

## Containment

1. Run `./scripts/chaos/redis_down.sh` in staging to rehearse behavior.
2. In production, disable Redis usage by unsetting `REDIS_URL` or routing traffic through nodes with in-memory limiter only.

## Mitigation

- Restart Redis or fail over to a replica.
- After recovery, clear stale rate-limit keys if needed (`redis-cli FLUSHALL` in non-prod; targeted deletes in prod).

## Verification

- `/healthz` and `/readyz` return 200.
- 429 rate returns to baseline.
- Metrics: `http_5xx_total` stable; circuit metrics show closed state.
