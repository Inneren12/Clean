from __future__ import annotations

import inspect
from typing import Any, Callable

import anyio

from app.infra.stripe_resilience import stripe_circuit
from app.settings import settings


class StripeClient:
    def __init__(
        self,
        *,
        secret_key: str | None,
        webhook_secret: str | None,
        stripe_sdk: Any | None = None,
    ) -> None:
        if stripe_sdk is None:
            import stripe as stripe_sdk  # type: ignore

        self.stripe = stripe_sdk
        self.secret_key = secret_key
        self.webhook_secret = webhook_secret

    async def _call(self, fn: Callable[[], Any]) -> Any:
        return await stripe_circuit.call(lambda: anyio.to_thread.run_sync(fn))

    async def create_checkout_session(
        self,
        *,
        amount_cents: int,
        currency: str,
        success_url: str,
        cancel_url: str,
        metadata: dict[str, str] | None = None,
        product_name: str = "Cleaning deposit",
        payment_intent_metadata: dict[str, str] | None = None,
        customer_email: str | None = None,
    ) -> Any:
        if not self.secret_key:
            raise ValueError("Stripe secret key not configured")

        self.stripe.api_key = self.secret_key
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
        return await self._call(lambda: self.stripe.checkout.Session.create(**payload))

    async def create_subscription_checkout_session(
        self,
        *,
        price_cents: int,
        currency: str,
        success_url: str,
        cancel_url: str,
        metadata: dict[str, str] | None = None,
        customer: str | None = None,
        price_id: str | None = None,
        plan_name: str = "SaaS Subscription",
    ) -> Any:
        if not self.secret_key:
            raise ValueError("Stripe secret key not configured")

        self.stripe.api_key = self.secret_key
        price_configuration: dict[str, Any]
        if price_id:
            price_configuration = {"price": price_id}
        else:
            price_configuration = {
                "price_data": {
                    "currency": currency,
                    "product_data": {"name": plan_name},
                    "unit_amount": price_cents,
                    "recurring": {"interval": "month"},
                }
            }

        payload: dict[str, Any] = {
            "mode": "subscription",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "line_items": [price_configuration],
            "metadata": metadata or {},
        }
        if customer:
            payload["customer"] = customer
        return await self._call(lambda: self.stripe.checkout.Session.create(**payload))

    async def create_billing_portal_session(
        self,
        *,
        customer_id: str,
        return_url: str,
    ) -> Any:
        if not self.secret_key:
            raise ValueError("Stripe secret key not configured")

        self.stripe.api_key = self.secret_key
        return await self._call(
            lambda: self.stripe.billing_portal.Session.create(
                customer=customer_id,
                return_url=return_url,
            )
        )

    async def verify_webhook(self, payload: bytes, signature: str | None) -> Any:
        if not self.webhook_secret:
            raise ValueError("Stripe webhook secret not configured")
        if not signature:
            raise ValueError("Missing Stripe signature header")
        return await self._call(
            lambda: self.stripe.Webhook.construct_event(
                payload=payload, sig_header=signature, secret=self.webhook_secret
            )
        )


def resolve_client(app_state: Any) -> StripeClient:
    client = getattr(app_state, "stripe_client", None)
    if client is None:
        client = StripeClient(
            secret_key=settings.stripe_secret_key,
            webhook_secret=settings.stripe_webhook_secret,
        )
        app_state.stripe_client = client
    return client


async def call_stripe_client_method(client: Any, method_name: str, /, *args, **kwargs) -> Any:
    method = getattr(client, method_name)
    if method is None:
        raise AttributeError(f"Stripe client missing method {method_name}")
    result = method(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result
