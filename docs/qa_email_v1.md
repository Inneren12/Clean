# QA — Email Workflow v1

## Preconditions
- Admin basic auth configured: `ADMIN_BASIC_USERNAME`, `ADMIN_BASIC_PASSWORD`.
- Email sending mode configured: `EMAIL_MODE` or equivalent adapter wiring available.
- Application running via `make up` or `docker-compose up`.

> Note: When `EMAIL_MODE=off`, adapters should not emit outbound traffic and must skip creating `email_events` records.

## Scenarios
1. **Booking pending email**
   - Create a lead then POST `/v1/bookings` with the lead ID.
   - Verify a pending email is attempted; booking creation succeeds even if email fails.
2. **Reminder scan idempotency**
   - Seed a confirmed booking within the next 24 hours.
   - Call `POST /v1/admin/email-scan` twice with basic auth.
   - First call reports `sent > 0`; second call reports `sent == 0` and no duplicate `email_events` rows.
3. **Resend last email**
   - Ensure at least one `email_events` row for a booking.
   - Call `POST /v1/admin/bookings/{booking_id}/resend-last-email` with basic auth.
   - Receive 202 response and a new `email_events` entry.
4. **Failure resilience**
   - Configure the email adapter to raise errors.
   - Booking creation and admin resend still return success status codes (201/202) and do not crash the API.

## Commands
- Run full backend tests:
  - `pytest -q`
- Targeted email workflow tests:
  - `pytest -q tests/test_email_workflow.py`
- Targeted migration tests:
  - `pytest -q tests/test_migrations.py`
