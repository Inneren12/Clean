# Incident playbook — Stripe webhook failures

## Signal

- `webhook_errors_total` increasing; Stripe dashboard shows failed deliveries.
- Logs contain `stripe_webhook_*` warnings or `Invalid signature` errors.

## Containment

1. Verify `STRIPE_WEBHOOK_SECRET` matches the active endpoint in Stripe.
2. Pause retries in Stripe if handlers are consistently failing (prevents thundering herd).
3. Keep `/v1/payments/stripe/webhook` reachable; avoid blocking IPs during investigation.

## Mitigation

- Replay events via Stripe dashboard after fixing secrets.
- For processing errors, inspect invoice/order state and repair records manually if needed.
- Increase logging verbosity temporarily (`LOG_LEVEL=DEBUG`) only in staging when reproducing.

## Verification

- New webhook deliveries succeed; `webhook_events_total{result="processed"}` increases.
- Error rate returns to baseline; Stripe dashboard shows green for the endpoint.
- Payment/invoice state matches Stripe source of truth.
