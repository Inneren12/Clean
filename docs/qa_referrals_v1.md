# QA Playbook: Referrals v1

Quick checks to confirm referral behavior aligns with Sprint G (credit on booking confirmation or paid deposit).

## Happy path: manual confirmation
1. Create a referrer lead via `/v1/leads` and capture the returned `referral_code`.
2. Create a referred lead by POSTing `/v1/leads` with `referral_code` set.
   - Expect `201` and `referred_by_code` stored on the new lead; no `referral_credits` rows yet.
3. Create a booking for the referred lead and confirm it via `/v1/admin/bookings/{id}/confirm` (admin/dispatcher auth).
   - Expect the booking status to be `CONFIRMED`.
   - Verify exactly one `referral_credits` row exists for the referrer/referred pairing.

## Deposit webhook path
1. Create a referred lead as above.
2. Create a booking that requires a deposit (e.g., pass a `DepositDecision(required=True, deposit_cents=5000)` if calling the service layer directly).
3. Call `mark_deposit_paid` (or trigger the Stripe webhook) with matching `checkout_session_id`/`payment_intent_id`.
   - Expect booking status `CONFIRMED` and `deposit_status="paid"`.
   - Confirm a single `referral_credits` record is inserted (webhook retries remain idempotent).

## Anti-abuse
- Unique constraint on `referral_credits.referred_lead_id` enforces one credit per new client.
- Invalid referral codes on `/v1/leads` return `400` to prevent silent attribution errors.
