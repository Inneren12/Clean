# SaaS Billing

This service supports organization-level subscription billing via Stripe Checkout and the billing portal. Plans are defined in code (`app/domain/saas/plans.py`) with limits for workers, monthly bookings, and storage.

## Data model
- **organization_billing**: Tracks the active plan, Stripe customer and subscription IDs, status, and period end per organization.
- **organization_usage_events**: Lightweight audit of usage increments (workers created, bookings per month, storage bytes uploaded) keyed by organization.

## Endpoints
- `POST /v1/billing/checkout` — Starts a Stripe subscription checkout for the requested plan. Metadata includes the organization ID for webhook reconciliation.
- `GET /v1/billing/portal` — Returns a Stripe billing portal link for the organization’s customer record.
- `GET /v1/billing/status` — Reports the active plan, limits, usage snapshot, and subscription status/period end.

All billing routes require a SaaS identity (Bearer token) to resolve the organization.

## Webhooks
Stripe webhooks at `/v1/payments/stripe/webhook` process subscription lifecycle events. Idempotency is enforced via the `stripe_events` table; replayed events with the same payload hash are ignored.

Handled events:
- `customer.subscription.*` updates organization_billing with plan, status, subscription/customer IDs, and period end.
- Subscription checkout session events (`checkout.session.*` with `mode=subscription`) hydrate the same billing record.

## Plan enforcement
Creation flows for workers, bookings, and photo uploads run entitlement checks against the organization’s active plan. Exceeding limits returns `402 Payment Required` with a descriptive error. Usage increments are recorded in `organization_usage_events` after successful actions.
