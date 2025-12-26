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

## Slot bookings (Sprint B)

- Slot search: `GET /v1/slots?date=YYYY-MM-DD&time_on_site_hours=H[.H]&postal_code=XYZ` returns 30-minute slots between 09:00–18:00 **America/Edmonton** (UTC timestamps in responses) with a 30-minute buffer between jobs.
- Booking creation: `POST /v1/bookings` with `starts_at` (ISO8601) and `time_on_site_hours` creates a `PENDING` booking and removes the slot from future searches.
- Cleanup: `POST /v1/admin/cleanup` (Basic auth using `ADMIN_BASIC_USERNAME`/`ADMIN_BASIC_PASSWORD`) deletes `PENDING` bookings older than 30 minutes so cron/Cloudflare Scheduler can call it.
- Email workflow v1: booking creation sends a pending notification when a lead email is present. `POST /v1/admin/email-scan` delivers 24h reminders once per booking using `email_events` dedupe, and `POST /v1/admin/bookings/{booking_id}/resend-last-email` replays the latest message for troubleshooting.
- Lead pipeline: admin endpoints use deterministic statuses (`NEW → CONTACTED → BOOKED → DONE/CANCELLED`). `POST /v1/admin/leads/{lead_id}/status` transitions a lead and `GET /v1/admin/leads?status=CONTACTED` filters the admin list.
- Frontend: after an estimate, the web UI shows the next three days of slots and books directly via the API.

## Deposits (Sprint E)

- Policy: deposits are required for weekend bookings (America/Edmonton day), move-out/empty or deep cleans, and new clients (no prior CONFIRMED/DONE bookings). The decision and reasons are stored on each booking.
- Amount: `DEPOSIT_PERCENT` (default 25%) of the lead's `estimate_snapshot.total_before_tax`, charged in `DEPOSIT_CURRENCY` (default CAD). Deposit URLs can include `{CHECKOUT_SESSION_ID}` and `{BOOKING_ID}` placeholders.
- Checkout: `POST /v1/bookings` returns a `checkout_url` when a deposit is required. Configure `STRIPE_SECRET_KEY`, `STRIPE_SUCCESS_URL`, and `STRIPE_CANCEL_URL` for live links. Booking creation is transactional—if Stripe is unavailable or checkout creation fails, no pending booking remains to block the slot.
- Webhook: `POST /v1/stripe/webhook` verifies the Stripe signature (`STRIPE_WEBHOOK_SECRET`). `checkout.session.completed` with `payment_status=paid` confirms the booking; expired/failed payments cancel the pending booking to free the slot.

### Cancellation / refund policy

- Unpaid deposits auto-cancel when the checkout session expires or via the 30-minute pending cleanup job.
- Paid deposits confirm the booking. For cancellations more than 24 hours before the start time, process manual refunds in Stripe; within 24 hours, deposits are non-refundable for this MVP.

## Analytics (Sprint F)

- Event log: `event_logs` captures `lead_created`, `booking_created`, `booking_confirmed`, and `job_completed` events with UTM/referrer context for conversion tracking.
- Metrics: `GET /v1/admin/metrics?from=...&to=...` (Basic auth) returns conversion counts, average estimated revenue, and estimator accuracy (estimated vs actual duration). Add `format=csv` to download the summary as CSV.
- Completion workflow: `POST /v1/admin/bookings/{booking_id}/complete` records `actual_duration_minutes` and marks the booking as `DONE` for estimator accuracy reporting. Use `POST /v1/admin/bookings/{booking_id}/confirm` to manually confirm non-deposit bookings.

## Referrals (Sprint G)

- Each lead receives a unique `referral_code` on creation. The web form accepts a `referral_code` field (or `ref`/`referral` query param) so new clients can attribute their booking.
- `/v1/leads` accepts `referral_code` to attribute the request. Invalid codes return `400` to avoid silent abuse.
- Credits are granted when a referred lead&apos;s booking is **confirmed** (manual confirm or paid deposit), not on lead submission. They are tracked in `referral_credits` with a unique `referred_lead_id` to enforce one credit per new client; admin leads responses include `referral_code`, `referred_by_code`, and `referral_credits` counts for visibility.

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

## Rate limiting backend

- Default: in-memory sliding window, suitable for single-instance deployments.
- Set `REDIS_URL=redis://user:pass@host:6379/0` to switch to Redis-backed limiting for
  horizontal scaling. The Redis backend enforces the window atomically via a Lua script
  (server time, single eval) and prunes expired entries automatically with key TTLs.
  Reset only removes `rate-limit:*` keys (no `FLUSHDB`) and the limiter fails open with
  a warning if Redis is unavailable.

## Assumptions

- If `EXPORT_MODE=sheets`, the API logs a warning and skips export until configured.
- `updated_at` timestamps are managed by the ORM on update (no database trigger).
- Webhook exports send the lead snapshot, structured inputs, and UTM fields.
- When both flat UTM fields and a `utm` object are provided, flat fields take precedence.

## Tests

```bash
pytest
```
