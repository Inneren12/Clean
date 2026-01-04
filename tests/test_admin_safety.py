from fastapi.testclient import TestClient

from app.main import app
from app.settings import settings


def _with_admin_creds():
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "password"


def test_admin_read_only_blocks_writes():
    settings.admin_read_only = True
    settings.admin_ip_allowlist_cidrs = []
    _with_admin_creds()
    client = TestClient(app)

    response = client.post("/v1/admin/pricing/reload", auth=("admin", "password"))

    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["detail"] == "Admin writes temporarily disabled"
    assert body["status"] == 409


def test_admin_ip_allowlist_blocks_non_matching_ip():
    settings.admin_read_only = False
    settings.admin_ip_allowlist_cidrs = ["10.0.0.0/24"]
    settings.trust_proxy_headers = True
    settings.trusted_proxy_ips = ["testclient"]
    _with_admin_creds()
    client = TestClient(app)

    response = client.get(
        "/v1/admin/profile",
        auth=("admin", "password"),
        headers={"x-forwarded-for": "192.0.2.1"},
    )

    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["detail"] == "Admin access restricted to allowlisted IPs"
    assert body["status"] == 403


def test_admin_allowlist_allows_gets_when_matched():
    settings.admin_read_only = False
    settings.admin_ip_allowlist_cidrs = ["127.0.0.0/8"]
    settings.trust_proxy_headers = True
    settings.trusted_proxy_ips = ["testclient"]
    _with_admin_creds()
    client = TestClient(app)

    response = client.get(
        "/v1/admin/profile",
        auth=("admin", "password"),
        headers={"x-forwarded-for": "127.0.0.5"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "admin"
