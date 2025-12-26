import base64

from app.settings import settings


def _auth_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_dispatcher_cannot_access_admin_only_endpoints(client):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"
    settings.dispatcher_basic_username = "dispatcher"
    settings.dispatcher_basic_password = "dispatch-secret"

    dispatcher_headers = _auth_header("dispatcher", "dispatch-secret")

    retention_response = client.post("/v1/admin/retention/cleanup", headers=dispatcher_headers)
    assert retention_response.status_code == 403

    metrics_response = client.get("/v1/admin/metrics", headers=dispatcher_headers)
    assert metrics_response.status_code == 403
