import base64

from app.settings import settings


def _basic_auth(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_set_ui_lang_sets_cookie_and_redirect(client):
    response = client.get("/ui/lang", params={"lang": "ru", "next": "/worker"}, follow_redirects=False)

    assert response.status_code in (302, 307)
    assert response.headers.get("location") == "/worker"
    assert response.cookies.get("ui_lang") == "ru"


def test_set_ui_lang_rejects_external_redirect(client):
    response = client.get("/ui/lang", params={"lang": "en", "next": "https://evil.com"})

    assert response.status_code == 400


def test_worker_page_renders_lang_toggle(client):
    previous_username = settings.worker_basic_username
    previous_password = settings.worker_basic_password
    previous_team = settings.worker_team_id
    previous_secret = settings.worker_portal_secret
    settings.worker_basic_username = "worker"
    settings.worker_basic_password = "secret"
    settings.worker_team_id = 1
    settings.worker_portal_secret = "test-worker-secret"

    try:
        client.post("/worker/login", headers=_basic_auth("worker", "secret"))

        response = client.get("/worker")

        assert response.status_code == 200
        assert "/ui/lang?lang=en" in response.text
        assert "/ui/lang?lang=ru" in response.text
    finally:
        settings.worker_basic_username = previous_username
        settings.worker_basic_password = previous_password
        settings.worker_team_id = previous_team
        settings.worker_portal_secret = previous_secret


def test_admin_page_renders_lang_toggle(client):
    previous_username = settings.admin_basic_username
    previous_password = settings.admin_basic_password
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"

    try:
        response = client.get("/v1/admin/observability", headers=_basic_auth("admin", "secret"))

        assert response.status_code == 200
        assert "/ui/lang?lang=en" in response.text
        assert "/ui/lang?lang=ru" in response.text
    finally:
        settings.admin_basic_username = previous_username
        settings.admin_basic_password = previous_password
