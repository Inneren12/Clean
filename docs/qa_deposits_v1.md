# QA — Deposits v1

## Preconditions
- Configure Stripe secrets: `STRIPE_SECRET_KEY`, `STRIPE_SUCCESS_URL`, `STRIPE_CANCEL_URL`, and `STRIPE_WEBHOOK_SECRET`.
- Use America/Edmonton dates when crafting booking start times (weekend detection is local to Edmonton).

## Happy path
1. Create a lead with a deep or move-out cleaning estimate.
2. POST `/v1/bookings` with `starts_at` on a weekend (Edmonton) and `lead_id` → expect `deposit_required=true`, `deposit_policy` containing reasons, `deposit_cents` populated, `deposit_status=pending`, and a `checkout_url`.
3. Simulate a Stripe webhook with a valid signature:
   - Event: `checkout.session.completed` with `payment_status=paid` and matching `id`/`payment_intent`.
   - Expect booking to transition to `CONFIRMED` and `deposit_status=paid`.

## Failure/edge cases
- Missing Stripe key: with a deposit-required request, expect HTTP 503 and **no** booking persisted.
- Checkout creation failure: force Stripe client to throw and expect HTTP 503, zero new bookings, and no emails sent.
- Unpaid session: `checkout.session.completed` events with `payment_status != paid` must leave the booking `PENDING` with `deposit_status=pending`.
- Expired/failed payment: `checkout.session.expired` or `payment_intent.payment_failed` sets `deposit_status` to `expired`/`failed` and cancels a `PENDING` booking to free the slot.

## Notes
- Booking creation is transactional: deposit failures never leave ghost `PENDING` bookings.
- Webhook signature verification uses the raw request body; replays are idempotent and safe.
