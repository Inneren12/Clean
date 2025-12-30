import base64

import pytest

from app.settings import settings


def _basic_auth(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.mark.anyio
async def test_csrf_enforced_when_testing_disabled(client):
    original = {
        "testing": settings.testing,
        "dispatcher_basic_username": settings.dispatcher_basic_username,
        "dispatcher_basic_password": settings.dispatcher_basic_password,
    }
    settings.testing = False
    settings.dispatcher_basic_username = "dispatch"
    settings.dispatcher_basic_password = "secret"

    try:
        headers = _basic_auth("dispatch", "secret")
        get_resp = client.get("/v1/admin/ui/workers", headers=headers)
        assert get_resp.status_code == 200

        post_resp = client.post(
            "/v1/admin/ui/workers/new",
            headers=headers,
            data={
                "name": "CSRF Test Worker",
                "phone": "+1 555-0000",
                "team_id": 1,
            },
            allow_redirects=False,
        )
        assert post_resp.status_code == 403
    finally:
        settings.testing = original["testing"]
        settings.dispatcher_basic_username = original["dispatcher_basic_username"]
        settings.dispatcher_basic_password = original["dispatcher_basic_password"]
