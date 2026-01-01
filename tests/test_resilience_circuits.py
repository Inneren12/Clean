import anyio
import pytest

from app import infra
from app.infra import stripe_resilience
from app.infra.email import EmailAdapter
from app.infra.storage.backends import S3StorageBackend
from app.infra.stripe_client import StripeClient
from app.settings import settings
from app.shared.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError


class _FakeStripeWebhook:
    @staticmethod
    def construct_event(payload, sig_header, secret):  # noqa: ANN001, ANN201
        return {"id": "evt_test"}


class _FakeStripeCheckoutSession:
    def __init__(self, parent):
        self._parent = parent

    def create(self, **kwargs):  # noqa: ANN001, ANN201
        if self._parent.fail:
            raise RuntimeError("checkout_fail")
        return {"id": "sess_test", "url": "https://example.com/checkout", **kwargs}


class _FakeStripe:
    def __init__(self) -> None:
        self.fail = True
        self.checkout = type(
            "Checkout",
            (),
            {
                "Session": type(
                    "Session",
                    (),
                    {"create": staticmethod(lambda **kwargs: _FakeStripeCheckoutSession(self).create(**kwargs))},
                )
            },
        )()
        self.billing_portal = type(
            "BillingPortal",
            (),
            {
                "Session": type(
                    "Session",
                    (),
                    {"create": staticmethod(lambda **kwargs: self._create_portal(**kwargs))},
                )
            },
        )()
        self.Webhook = _FakeStripeWebhook
        self.api_key = None

    def _create_portal(self, **kwargs):  # noqa: ANN001, ANN201
        if self.fail:
            raise RuntimeError("billing_fail")
        return {"url": "https://example.com/portal", **kwargs}


class _FakeS3Client:
    def __init__(self) -> None:
        self.fail = True

    def get_object(self, Bucket: str, Key: str):  # noqa: N802, ANN001, ANN201
        if self.fail:
            raise RuntimeError("s3_fail")
        return {"Body": type("_Body", (), {"read": lambda self: b"ok"})()}

    def delete_object(self, Bucket: str, Key: str):  # noqa: N802, ANN001
        if self.fail:
            raise RuntimeError("s3_fail")

    def get_paginator(self, name: str):  # noqa: ANN001, ANN201
        raise RuntimeError("not_used")

    def generate_presigned_url(self, *args, **kwargs):  # noqa: ANN001, ANN201
        if self.fail:
            raise RuntimeError("s3_fail")
        return "https://example.com/signed"


@pytest.mark.anyio
async def test_email_circuit_breaker_opens_and_recovers(monkeypatch):
    monkeypatch.setattr(settings, "email_mode", "sendgrid")
    monkeypatch.setattr(settings, "sendgrid_api_key", "key")
    monkeypatch.setattr(settings, "email_sender", "noreply@example.com")
    monkeypatch.setattr(settings, "email_circuit_failure_threshold", 2)
    monkeypatch.setattr(settings, "email_circuit_recovery_seconds", 0.05)

    adapter = EmailAdapter()

    async def fail_send(*args, **kwargs):  # noqa: ANN001
        raise RuntimeError("send_fail")

    async def ok_send(*args, **kwargs):  # noqa: ANN001
        return None

    monkeypatch.setattr(adapter, "_send_via_sendgrid", fail_send)

    with pytest.raises(RuntimeError):
        await adapter.send_email("user@example.com", "hi", "body")
    with pytest.raises(RuntimeError):
        await adapter.send_email("user@example.com", "hi", "body")

    result = await adapter.send_email("user@example.com", "hi", "body")
    assert result is False
    assert adapter._breaker.state == "open"  # noqa: SLF001

    await anyio.sleep(0.06)
    monkeypatch.setattr(adapter, "_send_via_sendgrid", ok_send)
    assert await adapter.send_email("user@example.com", "hi", "body") is True


@pytest.mark.anyio
async def test_stripe_circuit_breaker_opens_and_closes(monkeypatch):
    fake_stripe = _FakeStripe()
    monkeypatch.setattr(
        stripe_resilience,
        "stripe_circuit",
        CircuitBreaker(name="stripe-test", failure_threshold=2, recovery_time=0.05, window_seconds=1.0),
    )
    monkeypatch.setattr(infra.stripe_client, "stripe_circuit", stripe_resilience.stripe_circuit)

    client = StripeClient(secret_key="sk_test", webhook_secret="whsec_test", stripe_sdk=fake_stripe)

    with pytest.raises(RuntimeError):
        await client.create_checkout_session(amount_cents=1000, currency="usd", success_url="s", cancel_url="c")
    with pytest.raises(RuntimeError):
        await client.create_checkout_session(amount_cents=1000, currency="usd", success_url="s", cancel_url="c")

    with pytest.raises(CircuitBreakerOpenError):
        await client.create_checkout_session(amount_cents=1000, currency="usd", success_url="s", cancel_url="c")

    await anyio.sleep(0.06)
    fake_stripe.fail = False

    checkout = await client.create_checkout_session(
        amount_cents=1000,
        currency="usd",
        success_url="s",
        cancel_url="c",
    )
    assert checkout["id"] == "sess_test"


@pytest.mark.anyio
async def test_s3_circuit_breaker_guards_failures():
    fake_client = _FakeS3Client()
    backend = S3StorageBackend(
        bucket="bucket",
        access_key="access",
        secret_key="secret",
        enable_circuit_breaker=True,
        circuit_failure_threshold=2,
        circuit_recovery_seconds=0.05,
        circuit_window_seconds=1.0,
        client=fake_client,
    )

    with pytest.raises(RuntimeError):
        await backend.read(key="file.txt")
    with pytest.raises(RuntimeError):
        await backend.read(key="file.txt")

    with pytest.raises(CircuitBreakerOpenError):
        await backend.read(key="file.txt")

    await anyio.sleep(0.06)
    fake_client.fail = False

    content = await backend.read(key="file.txt")
    assert content == b"ok"
