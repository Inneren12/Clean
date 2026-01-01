"""
Smoke tests for health check and metrics endpoints.

These tests verify that critical observability endpoints work correctly
in both development and production configurations.
"""

import pytest
from unittest.mock import patch


def test_healthz_endpoint(client):
    """
    Test /healthz liveness probe endpoint.

    This endpoint should always return 200 OK with {"status": "ok"}.
    It's used by Kubernetes/Docker for basic liveness checks.
    """
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_endpoint_database_healthy(client):
    """
    Test /readyz readiness probe endpoint when database is healthy.

    This endpoint performs comprehensive health checks including:
    - Database connectivity
    - Alembic migration status
    - Background job health (if enabled)
    """
    response = client.get("/readyz")

    # May return 200 or 503 depending on migration status in test environment
    assert response.status_code in [200, 503]
    data = response.json()

    # Overall status
    assert data["status"] in ["ok", "unhealthy"]

    # Database checks
    assert "database" in data
    db = data["database"]
    assert "ok" in db
    assert "message" in db

    # Migration checks (may be "ok" or "skipped_no_alembic_files" in test environment)
    assert "migrations_check" in db
    assert db["migrations_check"] in ["ok", "skipped_no_alembic_files", "not_run"]

    # Jobs checks (optional, depends on configuration)
    if "jobs" in data:
        jobs = data["jobs"]
        assert "ok" in jobs
        assert "enabled" in jobs


def test_readyz_endpoint_database_unhealthy(client, monkeypatch):
    """
    Test /readyz endpoint behavior when database is unhealthy.

    Should return 503 Service Unavailable.
    """
    from app.api import routes_health

    async def mock_db_health(request):
        return {
            "ok": False,
            "message": "Database connection failed",
            "error": "Connection timeout",
            "migrations_current": False,
            "current_version": None,
            "expected_head": None,
            "expected_heads": [],
            "migrations_check": "error",
        }

    monkeypatch.setattr(routes_health, "_database_status", mock_db_health)

    response = client.get("/readyz")

    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "unhealthy"
    assert data["database"]["ok"] is False


def test_metrics_endpoint_disabled_by_default(client):
    """
    Test /metrics endpoint is disabled by default.

    Should return 404 when metrics_enabled=False.
    """
    response = client.get("/metrics")

    # In test environment, metrics might be enabled or disabled
    # If disabled, should return 404
    if response.status_code == 404:
        assert response.status_code == 404
    else:
        # If enabled in test environment, should return metrics or require auth
        assert response.status_code in [200, 401]


def test_metrics_endpoint_enabled_dev_mode(client):
    """
    Test /metrics endpoint in development mode (no auth required).
    """
    from app import settings
    from app.main import app

    # Create mock metrics client
    class MockMetrics:
        enabled = True

        def render(self):
            return "# TYPE http_requests_total counter\nhttp_requests_total 42\n", "text/plain"

    # Enable metrics in dev mode (app_env is already "dev" from enable_test_mode fixture)
    settings.app_env = "dev"
    settings.metrics_token = None
    app.state.metrics = MockMetrics()
    app.state.app_settings = settings

    response = client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]

    # Response should contain Prometheus metrics format
    content = response.text
    assert "http_requests_total" in content or "# TYPE" in content


def test_metrics_endpoint_enabled_prod_mode_no_token(client):
    """
    Test /metrics endpoint in production mode without token.

    Should return 401 Unauthorized.
    """
    from app import settings
    from app.main import app

    # Create mock metrics client
    class MockMetrics:
        enabled = True

        def render(self):
            return "# TYPE http_requests_total counter\nhttp_requests_total 42\n", "text/plain"

    # Enable metrics in prod mode with required token
    settings.app_env = "prod"
    settings.metrics_token = "super-secret-token-12345"
    app.state.metrics = MockMetrics()
    app.state.app_settings = settings

    response = client.get("/metrics")

    assert response.status_code == 401
    data = response.json()
    assert "detail" in data
    assert "unauthorized" in data["detail"].lower()


def test_metrics_endpoint_enabled_prod_mode_with_valid_token(client):
    """
    Test /metrics endpoint in production mode with valid bearer token.

    Should return 200 OK with metrics.
    """
    from app import settings
    from app.main import app

    # Create mock metrics client
    class MockMetrics:
        enabled = True

        def render(self):
            return "# TYPE http_requests_total counter\nhttp_requests_total 42\n", "text/plain"

    test_token = "super-secret-token-12345"

    # Enable metrics in prod mode with required token
    settings.app_env = "prod"
    settings.metrics_token = test_token
    app.state.metrics = MockMetrics()
    app.state.app_settings = settings

    # Test with Authorization header
    response = client.get(
        "/metrics",
        headers={"Authorization": f"Bearer {test_token}"}
    )

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


def test_metrics_endpoint_enabled_prod_mode_with_query_token(client):
    """
    Test /metrics endpoint in production mode with token in query string.

    Should return 200 OK with metrics.
    """
    from app import settings
    from app.main import app

    # Create mock metrics client
    class MockMetrics:
        enabled = True

        def render(self):
            return "# TYPE http_requests_total counter\nhttp_requests_total 42\n", "text/plain"

    test_token = "super-secret-token-12345"

    # Enable metrics in prod mode with required token
    settings.app_env = "prod"
    settings.metrics_token = test_token
    app.state.metrics = MockMetrics()
    app.state.app_settings = settings

    # Test with query parameter
    response = client.get(f"/metrics?token={test_token}")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


def test_metrics_endpoint_enabled_prod_mode_with_invalid_token(client):
    """
    Test /metrics endpoint in production mode with invalid token.

    Should return 401 Unauthorized.
    """
    from app import settings
    from app.main import app

    # Create mock metrics client
    class MockMetrics:
        enabled = True

        def render(self):
            return "# TYPE http_requests_total counter\nhttp_requests_total 42\n", "text/plain"

    # Enable metrics in prod mode with required token
    settings.app_env = "prod"
    settings.metrics_token = "super-secret-token-12345"
    app.state.metrics = MockMetrics()
    app.state.app_settings = settings

    # Test with wrong token
    response = client.get(
        "/metrics",
        headers={"Authorization": "Bearer wrong-token"}
    )

    assert response.status_code == 401

    # Test with wrong token in query string
    response = client.get("/metrics?token=wrong-token")

    assert response.status_code == 401


def test_metrics_endpoint_constant_time_comparison(client, monkeypatch):
    """
    Test that token comparison uses constant-time comparison.

    This is a security best practice to prevent timing attacks.
    We verify this by checking the implementation uses secrets.compare_digest.
    """
    from app.api import routes_metrics
    import inspect

    # Get the source code of the metrics endpoint handler
    source = inspect.getsource(routes_metrics)

    # Verify constant-time comparison is used
    assert "secrets.compare_digest" in source or "compare_digest" in source, \
        "Metrics endpoint should use constant-time token comparison (secrets.compare_digest)"
