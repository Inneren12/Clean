import logging
import time

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

logger = logging.getLogger(__name__)


class Metrics:
    def __init__(self, enabled: bool = False) -> None:
        self._configure(enabled)

    def _configure(self, enabled: bool) -> None:
        self.enabled = enabled
        self.registry = CollectorRegistry(auto_describe=True)
        if not enabled:
            self.webhook_events = None
            self.webhook_errors = None
            self.email_jobs = None
            self.email_notifications = None
            self.email_dlq = None
            self.bookings = None
            self.http_5xx = None
            self.http_latency = None
            self.job_heartbeat = None
            self.job_last_success = None
            self.job_runner_up = None
            self.circuit_state = None
            return

        self.webhook_events = Counter(
            "webhook_events_total",
            "Webhook events processed by result.",
            ["result"],
            registry=self.registry,
        )
        self.webhook_errors = Counter(
            "webhook_errors_total",
            "Webhook errors by type (low cardinality).",
            ["type"],
            registry=self.registry,
        )
        self.email_jobs = Counter(
            "email_jobs_total",
            "Email job outcomes per job name.",
            ["job", "status"],
            registry=self.registry,
        )
        self.email_notifications = Counter(
            "email_notifications_total",
            "Email delivery attempts by template and status.",
            ["template", "status"],
            registry=self.registry,
        )
        self.email_dlq = Gauge(
            "email_dlq_messages",
            "Email dead-letter queue depth by status.",
            ["status"],
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
        self.http_latency = Histogram(
            "http_request_latency_seconds",
            "HTTP request latency in seconds.",
            ["method", "path", "status_class"],
            buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
            registry=self.registry,
        )
        self.job_heartbeat = Gauge(
            "job_last_heartbeat_timestamp",
            "Unix timestamp for the latest job heartbeat.",
            ["job"],
            registry=self.registry,
        )
        self.job_last_success = Gauge(
            "job_last_success_timestamp",
            "Unix timestamp for the latest successful job loop.",
            ["job"],
            registry=self.registry,
        )
        self.job_runner_up = Gauge(
            "job_runner_up",
            "Job runner liveness indicator (1=recent heartbeat).",
            ["job"],
            registry=self.registry,
        )
        self.circuit_state = Gauge(
            "circuit_state",
            "Circuit breaker state (0=closed, 0.5=half-open, 1=open).",
            ["circuit"],
            registry=self.registry,
        )

    def record_webhook(self, result: str) -> None:
        if not self.enabled or self.webhook_events is None:
            return
        self.webhook_events.labels(result=result).inc()

    def record_webhook_error(self, error_type: str) -> None:
        if not self.enabled or self.webhook_errors is None:
            return
        safe_type = error_type or "unknown"
        self.webhook_errors.labels(type=safe_type).inc()

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

    def record_http_latency(self, method: str, path: str, status_code: int, duration_seconds: float) -> None:
        if not self.enabled or self.http_latency is None:
            return
        status_class = f"{status_code // 100}xx" if status_code else "unknown"
        duration_seconds = max(0.0, float(duration_seconds))
        self.http_latency.labels(method=method, path=path, status_class=status_class).observe(
            duration_seconds
        )

    def record_job_heartbeat(self, job: str, timestamp: float | None = None) -> None:
        if not self.enabled or self.job_heartbeat is None or self.job_runner_up is None:
            return
        ts = timestamp if timestamp is not None else time.time()
        self.job_heartbeat.labels(job=job).set(ts)
        self.job_runner_up.labels(job=job).set(1)

    def record_job_success(self, job: str, timestamp: float | None = None) -> None:
        if not self.enabled or self.job_last_success is None:
            return
        ts = timestamp if timestamp is not None else time.time()
        self.job_last_success.labels(job=job).set(ts)

    def record_email_notification(self, template: str, status: str, count: int = 1) -> None:
        if not self.enabled or self.email_notifications is None:
            return
        if count <= 0:
            return
        safe_template = template or "unknown"
        self.email_notifications.labels(template=safe_template, status=status).inc(count)

    def set_email_dlq_depth(self, status: str, count: int) -> None:
        if not self.enabled or self.email_dlq is None:
            return
        self.email_dlq.labels(status=status).set(max(0, count))

    def record_circuit_state(self, circuit: str, state: str) -> None:
        if not self.enabled or self.circuit_state is None:
            return
        value = {"closed": 0, "half_open": 0.5, "open": 1}.get(state, -1)
        self.circuit_state.labels(circuit=circuit).set(value)

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
