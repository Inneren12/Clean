from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings


def test_cors_strict_blocks_when_empty():
    app = create_app(Settings(app_env="prod", strict_cors=True, cors_origins=[]))
    with TestClient(app) as client:
        response = client.get("/healthz", headers={"Origin": "https://evil.com"})
        assert "access-control-allow-origin" not in response.headers


def test_cors_allows_configured_origin():
    app = create_app(Settings(app_env="prod", cors_origins=["https://example.com"]))
    with TestClient(app) as client:
        response = client.get("/healthz", headers={"Origin": "https://example.com"})
        assert response.headers["access-control-allow-origin"] == "https://example.com"
