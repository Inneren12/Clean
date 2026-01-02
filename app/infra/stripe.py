from __future__ import annotations

from typing import Any

import anyio

from app.infra.stripe_resilience import stripe_circuit
from app.shared.circuit_breaker import CircuitBreakerOpenError


def resolve_client(app_state: Any):
    client = getattr(app_state, "stripe_client", None)
    if client is None:
        import stripe as stripe_sdk

        client = stripe_sdk
        app_state.stripe_client = client
    return client


async def create_checkout_session(
    stripe_client: Any,
    secret_key: str,
    amount_cents: int,
    currency: str,
    success_url: str,
    cancel_url: str,
    metadata: dict[str, str] | None = None,
    product_name: str = "Cleaning deposit",
    payment_intent_metadata: dict[str, str] | None = None,
    customer_email: str | None = None,
):
    stripe_client.api_key = secret_key
    payload = {
        "mode": "payment",
        "payment_method_types": ["card"],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "line_items": [
            {
                "price_data": {
                    "currency": currency,
                    "product_data": {"name": product_name},
                    "unit_amount": amount_cents,
                },
                "quantity": 1,
            }
        ],
        "metadata": metadata or {},
    }
    if payment_intent_metadata:
        payload["payment_intent_data"] = {"metadata": payment_intent_metadata}
    if customer_email:
        payload["customer_email"] = customer_email

    try:
        return await stripe_circuit.call(
            lambda: anyio.to_thread.run_sync(lambda: stripe_client.checkout.Session.create(**payload))
        )
    except CircuitBreakerOpenError:
        raise


async def parse_webhook_event(
    stripe_client: Any, payload: bytes, signature: str | None, webhook_secret: str
):
    if not signature:
        raise ValueError("Missing Stripe signature header")
    return await stripe_circuit.call(
        lambda: anyio.to_thread.run_sync(
            lambda: stripe_client.Webhook.construct_event(
                payload=payload, sig_header=signature, secret=webhook_secret
            )
        )
    )
