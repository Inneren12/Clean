"""Tests for enhanced global search v2."""

import base64

import pytest

pytestmark = [pytest.mark.postgres, pytest.mark.smoke]


def _auth_headers(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture()
def admin_credentials():
    from app.settings import settings
    original_username = settings.admin_basic_username
    original_password = settings.admin_basic_password
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"
    try:
        yield
    finally:
        settings.admin_basic_username = original_username
        settings.admin_basic_password = original_password


def test_search_returns_results(client, admin_credentials):
    """Test that search returns results."""
    response = client.get(
        "/v1/admin/search?q=test",
        headers=_auth_headers("admin", "secret"),
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


def test_search_includes_relevance_score(client, admin_credentials):
    """Test that search results include relevance scores."""
    response = client.get(
        "/v1/admin/search?q=test",
        headers=_auth_headers("admin", "secret"),
    )
    assert response.status_code == 200
    data = response.json()
    for item in data:
        assert "relevance_score" in item
        assert isinstance(item["relevance_score"], int)
        assert item["relevance_score"] >= 0


def test_search_includes_quick_actions(client, admin_credentials):
    """Test that search results include quick actions."""
    response = client.get(
        "/v1/admin/search?q=test",
        headers=_auth_headers("admin", "secret"),
    )
    assert response.status_code == 200
    data = response.json()
    for item in data:
        assert "quick_actions" in item
        assert isinstance(item["quick_actions"], list)


def test_search_empty_query(client, admin_credentials):
    """Test search with empty query returns validation error."""
    response = client.get(
        "/v1/admin/search?q=",
        headers=_auth_headers("admin", "secret"),
    )
    # Empty query should return 422 validation error (q must have min length)
    assert response.status_code == 422


def test_search_respects_limit(client, admin_credentials):
    """Test search respects limit parameter."""
    response = client.get(
        "/v1/admin/search?q=test&limit=5",
        headers=_auth_headers("admin", "secret"),
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) <= 5


def test_search_requires_auth(client):
    """Test that search requires authentication."""
    response = client.get("/v1/admin/search?q=test")
    assert response.status_code == 401


def test_search_result_structure(client, admin_credentials):
    """Test search result has expected structure."""
    response = client.get(
        "/v1/admin/search?q=test&limit=1",
        headers=_auth_headers("admin", "secret"),
    )
    assert response.status_code == 200
    data = response.json()
    if data:
        item = data[0]
        assert "kind" in item
        assert "ref" in item
        assert "label" in item
        assert "created_at" in item
        assert "relevance_score" in item
        assert "quick_actions" in item
