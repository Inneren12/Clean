# Release Gates (v1)

Run these commands before promoting a release. They mirror CI coverage and add manual checks for secrets, auth, and CORS.

## Backend
```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
alembic upgrade head
pytest
```

## Frontend (web)
```bash
cd web
npm ci
npm run build
```

## Smoke checks
- Admin leads (auth + CORS):
  ```bash
  curl -i -u "$ADMIN_BASIC_USERNAME:$ADMIN_BASIC_PASSWORD" "${API_BASE:-http://localhost:8000}/v1/admin/leads"
  ```
- Dispatcher restriction (expect 403):
  ```bash
  curl -i -u "$DISPATCHER_BASIC_USERNAME:$DISPATCHER_BASIC_PASSWORD" "${API_BASE:-http://localhost:8000}/v1/admin/metrics"
  ```
- CORS preflight (staging/prod):
  ```bash
  curl -i -X OPTIONS "${API_BASE}/v1/estimate" \
    -H "Origin: ${PAGES_ORIGIN}" \
    -H "Access-Control-Request-Method: POST"
  ```

## Release blocker criteria
- Any failing command above.
- Missing required secrets: `DATABASE_URL`, `ADMIN_BASIC_*`, `DISPATCHER_BASIC_*`, `PRICING_CONFIG_PATH`, `STRICT_CORS=true` with matching `CORS_ORIGINS`, Stripe keys/URLs when deposits are enabled, email/export provider keys when those modes are on.
