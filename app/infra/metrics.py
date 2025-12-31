import logging

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, generate_latest

logger = logging.getLogger(__name__)


class Metrics:
    def __init__(self, enabled: bool = False) -> None:
        self._configure(enabled)

    def _configure(self, enabled: bool) -> None:
        self.enabled = enabled
        self.registry = CollectorRegistry(auto_describe=True)
        if not enabled:
            self.webhook_events = None
            self.email_jobs = None
            self.bookings = None
            self.http_5xx = None
            return

        self.webhook_events = Counter(
            "webhook_events_total",
            "Webhook events processed by result.",
            ["result"],
            registry=self.registry,
        )
        self.email_jobs = Counter(
            "email_jobs_total",
            "Email job outcomes per job name.",
            ["job", "status"],
            registry=self.registry,
        )
        self.bookings = Counter(
            "bookings_total",
            "Booking lifecycle events.",
            ["action"],
            registry=self.registry,
        )
        self.http_5xx = Counter(
            "http_5xx_total",
            "HTTP responses with status >= 500.",
            ["method", "path"],
            registry=self.registry,
        )

    def record_webhook(self, result: str) -> None:
        if not self.enabled or self.webhook_events is None:
            return
        self.webhook_events.labels(result=result).inc()

    def record_email_job(self, job: str, status: str, count: int = 1) -> None:
        if not self.enabled or self.email_jobs is None:
            return
        if count <= 0:
            return
        self.email_jobs.labels(job=job, status=status).inc(count)

    def record_booking(self, action: str, count: int = 1) -> None:
        if not self.enabled or self.bookings is None:
            return
        if count <= 0:
            return
        self.bookings.labels(action=action).inc(count)

    def record_http_5xx(self, method: str, path: str) -> None:
        if not self.enabled or self.http_5xx is None:
            return
        self.http_5xx.labels(method=method, path=path).inc()

    def render(self) -> tuple[bytes, str]:
        if not self.enabled:
            return b"metrics_disabled 1\n", "text/plain; version=0.0.4"
        try:
            return generate_latest(self.registry), CONTENT_TYPE_LATEST
        except Exception:  # noqa: BLE001
            logger.exception("metrics_render_failed")
            return b"metrics_render_failed 1\n", "text/plain; version=0.0.4"


metrics = Metrics(enabled=False)


def configure_metrics(enabled: bool) -> Metrics:
    metrics._configure(enabled)
    return metrics
