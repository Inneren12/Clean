from app.infra.metrics import configure_metrics
from app.main import app
from app.settings import settings


def test_metrics_endpoint_requires_token_when_configured(client):
    settings.metrics_token = "secret-token"
    settings.app_env = "prod"
    settings.metrics_enabled = True
    app.state.metrics = configure_metrics(True)

    unauthorized = client.get("/metrics")
    assert unauthorized.status_code == 401

    authorized = client.get("/metrics", headers={"Authorization": "Bearer secret-token"})
    assert authorized.status_code == 200

    query_token = client.get("/metrics?token=secret-token")
    assert query_token.status_code == 200


def test_metrics_path_label_uses_route_template(client_no_raise):
    settings.metrics_token = None
    settings.app_env = "dev"
    configure_metrics(True)

    async def boom_handler(item_id: str):  # pragma: no cover - handler executed in request
        raise RuntimeError("boom")

    app.router.add_api_route("/boom/{item_id}", boom_handler, methods=["GET"])

    response = client_no_raise.get("/boom/123")
    assert response.status_code == 500

    samples = []
    for metric in app.state.metrics.http_5xx.collect():
        samples.extend(metric.samples)

    assert any(sample.labels.get("path") == "/boom/{item_id}" for sample in samples)

    app.router.routes = [route for route in app.router.routes if getattr(route, "path", None) != "/boom/{item_id}"]


def test_metrics_unmatched_path_uses_placeholder(client_no_raise):
    settings.metrics_token = None
    settings.app_env = "dev"
    configure_metrics(True)

    response = client_no_raise.get("/does-not-exist/12345/abc")
    assert response.status_code == 404

    samples = []
    for metric in app.state.metrics.http_latency.collect():
        samples.extend(metric.samples)

    assert any(sample.labels.get("path") == "unmatched" for sample in samples)
