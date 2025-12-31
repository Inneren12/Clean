from app.settings import settings


def test_metrics_endpoint_requires_token_when_configured(client):
    settings.metrics_token = "secret-token"

    unauthorized = client.get("/metrics")
    assert unauthorized.status_code == 401

    authorized = client.get("/metrics", headers={"Authorization": "Bearer secret-token"})
    assert authorized.status_code == 200
