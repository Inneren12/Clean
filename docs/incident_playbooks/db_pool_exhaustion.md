# Incident playbook — DB pool exhaustion

## Signal

- Elevated latencies, `psycopg.OperationalError: timeout` in logs.
- `/readyz` failing with DB connectivity issues.
- Postgres shows many active connections (`pg_stat_activity`).

## Containment

1. Scale API pods down to reduce connection pressure; keep jobs runner alive.
2. Increase pgbouncer/max connections temporarily if safe.
3. Use feature flags to disable non-critical traffic (export/webhooks) if needed.

## Mitigation

- Identify slow queries via Postgres `pg_stat_statements`; add indexes or reduce N+1 traffic.
- Ensure `POOL_SIZE` and `MAX_OVERFLOW` (if configured) match DB limits.
- Restart stuck nodes after verifying new pool settings.

## Verification

- `/readyz` green and `job_runner_up` remains 1.
- `pg_stat_activity` shows connections below target threshold; wait events no longer `ClientRead` heavy.
- k6 smoke (`scripts/load/saas_k6.js` with low VUs) passes latency budgets.
