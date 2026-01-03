from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.infra.email import EmailAdapter, NoopEmailAdapter, resolve_email_adapter
from app.infra.metrics import Metrics, configure_metrics
from app.infra.security import RateLimiter, create_rate_limiter
from app.infra.storage import StorageBackend, new_storage_backend
from app.infra.stripe_client import StripeClient


@dataclass
class AppServices:
    """Typed container for runtime services stored on `app.state.services`."""

    storage: StorageBackend
    email_adapter: EmailAdapter | NoopEmailAdapter
    stripe_client: StripeClient
    rate_limiter: RateLimiter
    metrics: Metrics


def build_app_services(app_settings, *, metrics: Metrics | None = None) -> AppServices:
    metrics_client = metrics or configure_metrics(app_settings.metrics_enabled)
    return AppServices(
        storage=new_storage_backend(),
        email_adapter=resolve_email_adapter(app_settings),
        stripe_client=StripeClient(
            secret_key=app_settings.stripe_secret_key,
            webhook_secret=app_settings.stripe_webhook_secret,
        ),
        rate_limiter=create_rate_limiter(app_settings),
        metrics=metrics_client,
    )


def resolve_services(container_like: Any) -> AppServices | None:
    if isinstance(container_like, AppServices):
        return container_like
    if container_like is None:
        return None
    state = getattr(container_like, "state", container_like)
    return getattr(state, "services", None)
