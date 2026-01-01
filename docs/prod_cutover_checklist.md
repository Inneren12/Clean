# Production cutover checklist (GO readiness)

Use this list before moving real production traffic.

## Config and secrets

- [ ] `.env.production` populated from `.env.production.example` and stored in secret manager.
- [ ] STRIPE and email credentials validated in staging; webhook endpoint secret matches Stripe dashboard.
- [ ] Redis/DB endpoints reachable from target cluster; firewalls updated.

## Migrations

- [ ] `alembic upgrade head` run against production DB.
- [ ] Backup taken immediately before migration (`pg_dump` custom format).
- [ ] Rollback tested in staging (`alembic downgrade -1` + app rollback image).

## Observability

- [ ] Alert rules installed for `/readyz` failures, 5xx spikes, webhook/email errors, and DLQ growth.
- [ ] `/metrics` scraped; dashboards show p95/p99 latency and DB pool utilization.
- [ ] Sentry/central logging receiving events from new build.

## Smoke load

- [ ] k6 smoke run (`scripts/load/saas_k6.js`) with low VUs passes thresholds (<5% errors, p95 within budget).
- [ ] Admin list endpoints remain under rate-limit budgets.

## Chaos drills

- [ ] `scripts/chaos/redis_down.sh` completes (no hangs; Redis recovers).
- [ ] `scripts/chaos/s3_degraded.sh` completes (API responsive while storage blocked).
- [ ] `scripts/chaos/email_down.sh` completes (email job times out quickly; DLQ collects failed attempts).

## Rollback plan

- [ ] Previous container image tagged and available.
- [ ] Restore plan documented (see `docs/backup_restore.md`), including Postgres dump location and object storage versioning.
- [ ] Communication template ready for rollback announcement.

## GO decision

- [ ] Ops lead + engineering sign-off.
- [ ] Monitoring steady for 30 minutes post-deploy with smoke load.
