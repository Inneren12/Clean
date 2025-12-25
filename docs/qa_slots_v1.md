# QA: Slots v1

## Definition of Done
- Alembic migration adds `teams` (default row) and `bookings` tables with indexes on `starts_at` and `status`.
- GET `/v1/slots` returns 30-minute stepped availability between 09:00–18:00, using estimator `time_on_site_hours` rounded to slot steps and honoring a 30-minute buffer between jobs.
- POST `/v1/bookings` stores a `PENDING` booking for the selected slot using the rounded duration.
- POST `/v1/admin/cleanup` removes `PENDING` bookings older than 30 minutes (admin basic auth required).
- Web booking flow shows the next three days of slots after an estimate and can create a booking.

## Verification commands
- Run unit and API coverage for slots and migrations:
  - `pytest tests/test_slots.py tests/test_admin_api.py tests/test_migrations.py`
- Manual cleanup check (requires configured `ADMIN_BASIC_USERNAME`/`ADMIN_BASIC_PASSWORD`):
  - `curl -u $ADMIN_BASIC_USERNAME:$ADMIN_BASIC_PASSWORD -X POST http://localhost:8000/v1/admin/cleanup`
- Frontend smoke (after `npm install` in `web/`):
  - `npm run lint` (or `npm run dev` to interactively verify slot UI)
