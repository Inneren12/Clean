# Cleaning Economy Bot API

FastAPI backend for the Economy MVP pricing and chat system.

## Setup (Docker-first, Sprint 2 canonical flow)

1. Copy the environment file (Docker uses the `postgres` hostname):

   ```bash
   cp .env.example .env
   ```

2. Start the stack:

   ```bash
   make up
   ```

   (Use `make dev` if you want logs in the foreground.)

3. Apply migrations (runs inside the API container so `DATABASE_URL` resolves):

   ```bash
   make migrate
   ```

   Some Docker Compose versions ignore/deny `depends_on: condition: service_healthy`.
   That is OK because `make migrate` waits for Postgres readiness and runs Alembic
   inside the API container.

4. Run the core endpoints:

   ```bash
   curl http://localhost:8000/healthz
   ```

   ```bash
   curl -X POST http://localhost:8000/v1/estimate \
     -H "Content-Type: application/json" \
     -d '{
       "beds": 2,
       "baths": 1.5,
       "cleaning_type": "deep",
       "heavy_grease": true,
       "multi_floor": true,
       "frequency": "weekly",
       "add_ons": {
         "oven": true,
         "fridge": false,
         "microwave": true,
         "cabinets": false,
         "windows_up_to_5": true,
         "balcony": false,
         "linen_beds": 2,
         "steam_armchair": 0,
         "steam_sofa_2": 1,
         "steam_sofa_3": 0,
         "steam_sectional": 0,
         "steam_mattress": 0,
         "carpet_spot": 1
       }
     }'
  ```

   Sample response (trimmed):

   ```json
   {
     "pricing_config_id": "economy",
     "pricing_config_version": "v1",
     "config_hash": "sha256:...",
     "team_size": 2,
     "total_before_tax": 282.75
   }
   ```

   ```bash
   curl -X POST http://localhost:8000/v1/chat/turn \
     -H "Content-Type: application/json" \
     -d '{
       "session_id": "session-123",
       "message": "Hi, I need a deep clean for a 2 bed 1.5 bath with oven and fridge weekly"
     }'
   ```

   ```bash
   curl -X POST http://localhost:8000/v1/leads \
     -H "Content-Type: application/json" \
     -d '{
       "name": "Jane Doe",
       "phone": "780-555-1234",
       "email": "jane@example.com",
       "postal_code": "T5J 0N3",
       "preferred_dates": ["Sat afternoon", "Sun morning"],
       "access_notes": "Buzz #1203",
       "structured_inputs": {
         "beds": 2,
         "baths": 2,
         "cleaning_type": "deep"
       },
       "estimate_snapshot": {
         "pricing_config_id": "economy",
         "pricing_config_version": "v1",
         "config_hash": "sha256:...",
         "rate": 35.0,
         "team_size": 2,
         "time_on_site_hours": 3.5,
         "billed_cleaner_hours": 7.0,
         "labor_cost": 245.0,
         "discount_amount": 12.25,
         "add_ons_cost": 50.0,
         "total_before_tax": 282.75,
         "assumptions": [],
         "missing_info": [],
         "confidence": 1.0
       }
     }'
   ```

## Host-based alternative (optional)

The canonical flow is Docker-first. If you run Alembic locally, you must point
`DATABASE_URL` at localhost instead of `postgres`:

```bash
export DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/cleaning
alembic upgrade head
```

## Developer shortcuts (Sprint 2)

```bash
make dev
make up
make down
make logs
make migrate
make psql
make test
```

## Sprint 2 Notes / Boundaries

- Chat sessions and leads are persisted in Postgres via SQLAlchemy async.
- Alembic migrations live in `alembic/`.
- `/v1/leads` captures booking/contact details with estimate snapshots.

## Web UI (chat tester)

The minimal Next.js chat UI lives in `web/`. It expects the API base URL in an
environment variable. This is a local Sprint 1 chat tester; before production use,
upgrade Next.js to a patched version.

```bash
cd web
cp .env.example .env.local
npm install
npm run dev
```

Environment:

```
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

Quick replies in the UI prefill the input so users can edit before sending.

## Troubleshooting

- Postgres not ready: `make logs` to inspect startup, then re-run `make migrate`.
- Port conflicts: stop the conflicting process or edit ports in `docker-compose.yml`.
- Reset DB volume (preferred): `make reset-db`.

## Error format (ProblemDetails)

```json
{
  "type": "https://example.com/problems/validation-error",
  "title": "Validation Error",
  "status": 422,
  "detail": "Request validation failed",
  "request_id": "8e3b0b5f-63c7-4596-9a9b-e4b6f1e5b6b0",
  "errors": [
    {
      "field": "beds",
      "message": "Input should be greater than or equal to 0"
    }
  ]
}
```

Other errors use the same envelope with `type` values such as
`https://example.com/problems/domain-error`,
`https://example.com/problems/rate-limit`, and
`https://example.com/problems/server-error`.

## Logging + privacy

- Logs are JSON formatted and redact phone numbers, emails, and street addresses.
- Do not log raw request bodies or full lead payloads.
- Prefer logging identifiers (lead_id, session_id) and status codes.

## Lead export (optional)

Configure outbound export via environment variables:

```
EXPORT_MODE=off|webhook|sheets
EXPORT_WEBHOOK_URL=https://example.com/lead-hook
EXPORT_WEBHOOK_TIMEOUT_SECONDS=5
EXPORT_WEBHOOK_MAX_RETRIES=3
EXPORT_WEBHOOK_BACKOFF_SECONDS=1.0
EXPORT_WEBHOOK_ALLOWED_HOSTS=hook.example.com,api.make.com
EXPORT_WEBHOOK_ALLOW_HTTP=false
EXPORT_WEBHOOK_BLOCK_PRIVATE_IPS=true
```

Webhook exports run best-effort in a background task and do not block lead creation.
Webhook validation enforces https by default, host allowlists, and blocks private IP ranges.

## CORS + proxy settings

```
APP_ENV=prod
STRICT_CORS=false
CORS_ORIGINS=https://yourdomain.com
TRUST_PROXY_HEADERS=false
TRUSTED_PROXY_IPS=203.0.113.10
TRUSTED_PROXY_CIDRS=203.0.113.0/24
```

- In `prod`, `CORS_ORIGINS` must be explicitly set for browser access.
- In `dev`, missing `CORS_ORIGINS` defaults to `http://localhost:3000` unless `STRICT_CORS=true`.
- When `TRUST_PROXY_HEADERS=true` and the request comes from a trusted proxy, the rate limiter
  keys by the first `X-Forwarded-For` address.

## Assumptions

- If `EXPORT_MODE=sheets`, the API logs a warning and skips export until configured.
- `updated_at` timestamps are managed by the ORM on update (no database trigger).
- Webhook exports send the lead snapshot, structured inputs, and UTM fields.
- When both flat UTM fields and a `utm` object are provided, flat fields take precedence.

## Tests

```bash
pytest
```
