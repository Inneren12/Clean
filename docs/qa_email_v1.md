# QA - Email workflow v1

## Preconditions
- Set `ADMIN_BASIC_USERNAME` / `ADMIN_BASIC_PASSWORD` for admin endpoints.
- Configure `EMAIL_MODE`/provider credentials when validating real deliveries; stub adapters are fine for local testing.

## Scenarios
1. **Booking pending notification**
   - Create a lead with an email, then book a slot via `POST /v1/bookings`.
   - Expect one `email_events` row with `booking_pending` and the pending copy.
2. **Reminder scan idempotency**
   - Seed a CONFIRMED booking starting within 24h.
   - Call `POST /v1/admin/email-scan` twice; first call sends a reminder, second returns `sent: 0` with no new `email_events` rows.
3. **Resend latest email**
   - Ensure a booking has at least one `email_events` row.
   - Call `POST /v1/admin/bookings/{booking_id}/resend-last-email` and verify another row is recorded plus one more outbound attempt.
4. **Failure resilience**
   - Swap in a failing `email_adapter` (or misconfigure provider) and create a lead/booking.
   - API responses should stay 201/202 even if email sending raises.

## Commands
- Run the suite: `make test`
- Targeted: `pytest tests/test_email_workflow.py`
