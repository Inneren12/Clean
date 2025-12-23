# Deploying Cleaning Economy Bot (Render)

This guide uses Render with a managed Postgres instance. It assumes Docker-based
builds for the API and separate static hosting for the Next.js landing if needed.

## 1) Create the database

1. Create a new PostgreSQL database in Render.
2. Copy the internal connection string.
3. Note the region so the API runs close to the DB.

## 2) Create the API service

1. Create a new **Web Service** from this repo.
2. Environment: **Docker**.
3. Set the **Start Command** to run migrations, then serve:

   ```bash
   alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

4. Add environment variables:

   - `DATABASE_URL`: Render Postgres connection string
   - `PRICING_CONFIG_PATH=pricing/economy_v1.json`
   - `RATE_LIMIT_PER_MINUTE=30`
   - `CORS_ORIGINS=https://your-frontend-domain`
   - `EXPORT_MODE=off|webhook|sheets`
   - `EXPORT_WEBHOOK_URL=https://example.com/lead-hook` (if webhook mode)
   - `EXPORT_WEBHOOK_TIMEOUT_SECONDS=5`
   - `EXPORT_WEBHOOK_MAX_RETRIES=3`
   - `EXPORT_WEBHOOK_BACKOFF_SECONDS=1.0`

5. Health check path: `/healthz`
6. Deploy the service.

## 3) Deploy the marketing site

If you serve the Next.js landing separately:

1. Deploy `web/` as a Static Site or Next.js service.
2. Set `NEXT_PUBLIC_API_BASE_URL` to your API URL.

## 4) Verification checklist

- `GET /healthz` returns `{ "status": "ok" }`.
- `POST /v1/estimate` returns pricing fields with `pricing_config_id` and `config_hash`.
- `POST /v1/leads` returns a `lead_id` and triggers export if enabled.

## 5) Backups

Render Postgres has automated backups. Keep a weekly export plan:

- Enable daily automated backups in Render.
- Monthly manual snapshot + offsite copy (S3 or secure storage).
- Verify restore procedures quarterly.
