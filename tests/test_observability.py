import pytest

from app.infra.metrics import configure_metrics, metrics
from app.main import app
from app.jobs.heartbeat import record_heartbeat
from app.shared.circuit_breaker import CircuitBreaker


def _remove_route(path: str) -> None:
    app.router.routes = [route for route in app.router.routes if getattr(route, "path", None) != path]


def test_http_latency_uses_route_template(client_no_raise):
    configure_metrics(True)

    async def ok_handler(item_id: str):  # pragma: no cover - executed via request
        return {"ok": True}

    route_path = "/latency/{item_id}"
    app.router.add_api_route(route_path, ok_handler, methods=["GET"])

    response = client_no_raise.get("/latency/123")
    assert response.status_code == 200

    samples = []
    for metric in app.state.metrics.http_latency.collect():
        samples.extend(metric.samples)

    assert any(sample.labels.get("path") == route_path for sample in samples)
    _remove_route(route_path)


@pytest.mark.anyio
async def test_job_heartbeat_metric(async_session_maker):
    configure_metrics(True)
    await record_heartbeat(async_session_maker, name="jobs-runner")

    samples = []
    for metric in metrics.job_heartbeat.collect():
        samples.extend(metric.samples)

    assert any(sample.labels.get("job") == "jobs-runner" for sample in samples)


@pytest.mark.anyio
async def test_circuit_state_metric_opens_on_failure():
    configure_metrics(True)
    breaker = CircuitBreaker(name="email-test", failure_threshold=1, recovery_time=5)

    async def fail():  # pragma: no cover - executed via breaker
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await breaker.call(fail)

    samples = []
    for metric in metrics.circuit_state.collect():
        samples.extend(metric.samples)

    assert any(
        sample.labels.get("circuit") == "email-test" and sample.value == 1 for sample in samples
    )


def test_email_notification_metric_labels_low_cardinality():
    configure_metrics(True)
    metrics.record_email_notification("invoice_sent", "failed")

    collected = metrics.email_notifications.collect()
    assert collected
    sample = collected[0].samples[0]
    assert set(sample.labels.keys()) == {"template", "status"}


def test_email_adapter_metric_labels_low_cardinality():
    configure_metrics(True)
    metrics.record_email_adapter("sent")

    collected = metrics.email_adapter_outcomes.collect()
    assert collected
    sample = collected[0].samples[0]
    assert set(sample.labels.keys()) == {"status"}


def test_outbox_depth_metric_labels_low_cardinality():
    configure_metrics(True)
    metrics.set_outbox_depth("pending", 3)

    collected = metrics.outbox_queue_depth.collect()
    assert collected
    sample = collected[0].samples[0]
    assert set(sample.labels.keys()) == {"status"}


def test_stripe_webhook_metric_labels_low_cardinality():
    configure_metrics(True)
    metrics.record_stripe_webhook("processed")

    collected = metrics.stripe_webhook_events.collect()
    assert collected
    sample = collected[0].samples[0]
    assert set(sample.labels.keys()) == {"outcome"}
