# Rollback runbook (production)

Use this runbook when a production deploy must be rolled back. It assumes Docker Compose–based releases and the `.env.production` file described in `docs/deploy_production.md`.

## 1) Identify rollback target

1. Locate the last known-good image tag (from CI build logs or the container registry).
2. Confirm the tag includes the migrations level that was previously in prod.

## 2) Roll back the application version

1. Point Compose to the known-good tag (example shown with an `IMAGE_TAG` override):

   ```bash
   IMAGE_TAG=2025-12-15-rc1 docker compose --env-file .env -f docker-compose.yml pull
   IMAGE_TAG=2025-12-15-rc1 docker compose --env-file .env -f docker-compose.yml up -d api jobs
   ```

2. Verify containers are running the expected hash:

   ```bash
   docker compose ps --format json | jq '.[].Image'
   ```

3. Smoke test `/healthz` and `/readyz` before re-enabling traffic.

## 3) Handle migrations

**Preferred:** ship a forward-fix migration. If the failed deploy introduced a migration, create a new migration that corrects the data/schema and redeploy using the normal rollout order.

**If downtime demands a quick rollback:**

1. Assess whether the new migration is backward compatible. If the schema change is additive (new columns/tables), downgrading is typically unnecessary—keep the migration and revert only the app image.
2. If the migration is breaking and must be undone:
   - Confirm a `downgrade()` exists for the latest revision.
   - Run the downgrade in isolation:

     ```bash
     docker compose --env-file .env -f docker-compose.yml run --rm api alembic downgrade -1
     ```

   - Re-run `/readyz` to ensure `migrations_current=true` after the downgrade.
3. Document the applied action in the incident log and schedule a forward-fix migration to reconcile any data drift.

## 4) Emergency disablement for exports/reports

If data leaks or performance issues involve exports or reports, immediately disable them while keeping the API online:

1. Set `EXPORT_MODE=off` in the secret store / `.env` used by production.
2. Redeploy the API with the updated environment:

   ```bash
   docker compose --env-file .env -f docker-compose.yml up -d api
   ```

3. Confirm the change took effect by hitting `/readyz` (to ensure the new container is live) and validating that export/report endpoints return 404/disabled responses.

## 5) Post-rollback validation

1. Run `/healthz` (liveness) and `/readyz` (DB + jobs + migrations) to confirm green status.
2. Check metrics scrape with the bearer token:

   ```bash
   curl -H "Authorization: Bearer $METRICS_TOKEN" https://api.example.com/metrics | head
   ```

3. Verify recent logs for errors in `api` and `jobs` containers:

   ```bash
   docker compose logs --since=10m api jobs | tail -n 200
   ```

4. Announce rollback completion in the incident channel and create a follow-up ticket for the forward-fix.
