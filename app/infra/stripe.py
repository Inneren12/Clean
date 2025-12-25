from __future__ import annotations

from typing import Any


def resolve_client(app_state: Any):
    client = getattr(app_state, "stripe_client", None)
    if client is None:
        import stripe as stripe_sdk

        client = stripe_sdk
        app_state.stripe_client = client
    return client


def create_checkout_session(
    stripe_client: Any,
    secret_key: str,
    amount_cents: int,
    currency: str,
    success_url: str,
    cancel_url: str,
    metadata: dict[str, str] | None = None,
):
    stripe_client.api_key = secret_key
    return stripe_client.checkout.Session.create(
        mode="payment",
        payment_method_types=["card"],
        success_url=success_url,
        cancel_url=cancel_url,
        line_items=[
            {
                "price_data": {
                    "currency": currency,
                    "product_data": {"name": "Cleaning deposit"},
                    "unit_amount": amount_cents,
                },
                "quantity": 1,
            }
        ],
        metadata=metadata or {},
    )


def parse_webhook_event(stripe_client: Any, payload: bytes, signature: str | None, webhook_secret: str):
    if not signature:
        raise ValueError("Missing Stripe signature header")
    return stripe_client.Webhook.construct_event(payload=payload, sig_header=signature, secret=webhook_secret)
