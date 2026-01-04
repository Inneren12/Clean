import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import create_app
from app.settings import Settings


def test_cors_strict_blocks_when_empty():
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        create_app(
            Settings(
                app_env="prod",
                strict_cors=True,
                cors_origins=[],
                auth_secret_key="super-secret",
                client_portal_secret="client-secret",
                worker_portal_secret="worker-secret",
                metrics_enabled=False,
            )
        )


def test_cors_allows_configured_origin():
    app = create_app(
        Settings(
            app_env="prod",
            cors_origins=["https://example.com"],
            auth_secret_key="super-secret",
            client_portal_secret="client-secret",
            worker_portal_secret="worker-secret",
            metrics_enabled=False,
        )
    )
    with TestClient(app) as client:
        response = client.get("/healthz", headers={"Origin": "https://example.com"})
        assert response.headers["access-control-allow-origin"] == "https://example.com"
