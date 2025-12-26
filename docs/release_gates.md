# Release Gates (v1)

Run these commands before promoting a release. They mirror CI coverage and add manual checks for secrets and auth.

## Backend
1. Install dependencies:
   ```bash
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```
2. Apply migrations (requires `DATABASE_URL`):
   ```bash
   alembic upgrade head
   ```
3. Run tests:
   ```bash
   pytest
   ```

## Frontend (web/)
1. Install and build:
   ```bash
   npm ci
   npm run build
   ```

## Docker compose smoke (local/staging)
1. Bring up stack and wait for health:
   ```bash
   make up
   curl http://localhost:8000/healthz
   ```

## Admin auth smoke
1. Verify admin/dispatcher credentials and CORS from the intended origin:
   ```bash
   curl -i -u "$ADMIN_BASIC_USERNAME:$ADMIN_BASIC_PASSWORD" "${API_BASE:-http://localhost:8000}/v1/admin/leads"
   ```

## Stripe webhook (if deposits enabled)
1. Trigger a signed webhook from Stripe CLI to the staging URL and confirm booking state updates.

## Release blocker criteria
- Any failing command above.
- Missing required secrets: `DATABASE_URL`, `ADMIN_BASIC_*`, `DISPATCHER_BASIC_*`, `PRICING_CONFIG_PATH`, `STRICT_CORS=true` with `CORS_ORIGINS` set, Stripe keys/URLs when deposits enabled, email/export provider keys when modes are on.
