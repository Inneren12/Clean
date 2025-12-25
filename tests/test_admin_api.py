import base64

from app.settings import settings


def _basic_auth_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_admin_leads_requires_auth(client):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"

    response = client.get("/v1/admin/leads")
    assert response.status_code == 401

    auth_headers = _basic_auth_header("admin", "secret")
    authorized = client.get("/v1/admin/leads", headers=auth_headers)
    assert authorized.status_code == 200
    assert isinstance(authorized.json(), list)
