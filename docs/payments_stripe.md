# Stripe invoice payments

## Configuration

Set the following environment variables to enable Stripe-based invoice payments:

- `STRIPE_SECRET_KEY` – secret API key for creating Checkout Sessions.
- `STRIPE_WEBHOOK_SECRET` – signing secret used to verify `/stripe/webhook` requests.
- `STRIPE_INVOICE_SUCCESS_URL` – URL users are redirected to after a successful payment. Supports `{CHECKOUT_SESSION_ID}` and `{INVOICE_ID}` placeholders.
- `STRIPE_INVOICE_CANCEL_URL` – URL users see after cancelling a checkout flow. Supports `{INVOICE_ID}`.

## Public payment flow

1. The public invoice page (`/i/{token}`) calls `POST /i/{token}/pay` to create a Stripe Checkout Session for the outstanding balance.
2. The API returns a redirect URL so the customer can complete payment in Stripe Checkout.
3. Stripe sends webhooks to `POST /stripe/webhook` for `checkout.session.completed`, `payment_intent.succeeded`, and `payment_intent.payment_failed` events. The webhook is idempotent by `event.id` and deduplicates payments by Stripe `payment_intent` IDs. `checkout.session.completed` events without a `payment_intent` are ignored.
4. Successful webhook events create `invoice_payments` records with `provider="stripe"` and update invoice status to `PARTIAL`/`PAID` in a single transaction.

## Local webhook testing

1. Install the Stripe CLI and log in: `stripe login`.
2. Start a listener that forwards to the FastAPI server:
   ```bash
   stripe listen --events checkout.session.completed,payment_intent.succeeded,payment_intent.payment_failed \
     --forward-to localhost:8000/stripe/webhook
   ```
3. Use the secret printed by the CLI to populate `STRIPE_WEBHOOK_SECRET` in your environment.
4. Trigger a test event from the CLI (for example `stripe trigger payment_intent.succeeded`) or by completing a Checkout Session.

## Notes

- Secrets must come from the environment or secret storage; never hardcode keys in code or tests.
- Webhook processing logs `succeeded`, `ignored`, and `error` outcomes to aid observability.
- Payments are idempotent both by Stripe event ID (via the `stripe_events` table) and by `provider_ref` (Stripe payment intent) to avoid duplicate `invoice_payments` rows.
- Invoice checkout uses the invoice currency uppercased for display and lowercased for the Stripe API. VOID invoices cannot be paid.
