"""Tests for operator work queues."""

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


def test_photo_queue_pending(client, admin_credentials):
    """Test fetching pending photos queue."""
    response = client.get(
        "/v1/admin/queue/photos?status=pending",
        headers=_auth_headers("admin", "secret"),
    )
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert "pending_count" in data
    assert "needs_retake_count" in data
    assert isinstance(data["items"], list)


def test_photo_queue_needs_retake(client, admin_credentials):
    """Test fetching photos needing retake."""
    response = client.get(
        "/v1/admin/queue/photos?status=needs_retake",
        headers=_auth_headers("admin", "secret"),
    )
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "needs_retake_count" in data


def test_invoice_queue_overdue(client, admin_credentials):
    """Test fetching overdue invoices queue."""
    response = client.get(
        "/v1/admin/queue/invoices?status=overdue",
        headers=_auth_headers("admin", "secret"),
    )
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert "overdue_count" in data
    assert "unpaid_count" in data
    assert isinstance(data["items"], list)


def test_invoice_queue_unpaid(client, admin_credentials):
    """Test fetching unpaid invoices queue."""
    response = client.get(
        "/v1/admin/queue/invoices?status=unpaid",
        headers=_auth_headers("admin", "secret"),
    )
    assert response.status_code == 200
    data = response.json()
    assert "unpaid_count" in data


def test_assignment_queue(client, admin_credentials):
    """Test fetching unassigned bookings queue."""
    response = client.get(
        "/v1/admin/queue/assignments?days_ahead=7",
        headers=_auth_headers("admin", "secret"),
    )
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert "urgent_count" in data
    assert isinstance(data["items"], list)


def test_assignment_queue_custom_window(client, admin_credentials):
    """Test assignment queue with custom look-ahead window."""
    response = client.get(
        "/v1/admin/queue/assignments?days_ahead=14",
        headers=_auth_headers("admin", "secret"),
    )
    assert response.status_code == 200
    data = response.json()
    assert "urgent_count" in data


def test_dlq_all(client, admin_credentials):
    """Test fetching all dead letter queue items."""
    response = client.get(
        "/v1/admin/queue/dlq?kind=all",
        headers=_auth_headers("admin", "secret"),
    )
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert "outbox_dead_count" in data
    assert "export_dead_count" in data
    assert isinstance(data["items"], list)


def test_dlq_outbox_only(client, admin_credentials):
    """Test fetching only outbox dead letters."""
    response = client.get(
        "/v1/admin/queue/dlq?kind=outbox",
        headers=_auth_headers("admin", "secret"),
    )
    assert response.status_code == 200
    data = response.json()
    assert "outbox_dead_count" in data


def test_dlq_export_only(client, admin_credentials):
    """Test fetching only export dead letters."""
    response = client.get(
        "/v1/admin/queue/dlq?kind=export",
        headers=_auth_headers("admin", "secret"),
    )
    assert response.status_code == 200
    data = response.json()
    assert "export_dead_count" in data


def test_queue_pagination(client, admin_credentials):
    """Test queue pagination works correctly."""
    response = client.get(
        "/v1/admin/queue/photos?limit=10&offset=0",
        headers=_auth_headers("admin", "secret"),
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) <= 10


def test_queue_requires_auth(client):
    """Test that queue endpoints require authentication."""
    response = client.get("/v1/admin/queue/photos")
    assert response.status_code == 401


def test_queue_quick_actions(client, admin_credentials):
    """Test that queue items include quick actions."""
    response = client.get(
        "/v1/admin/queue/photos?limit=1",
        headers=_auth_headers("admin", "secret"),
    )
    assert response.status_code == 200
    data = response.json()
    # Quick actions are always present (even if items are empty)
    assert "items" in data
    # If there are items, verify structure
    for item in data["items"]:
        assert "quick_actions" in item
        assert isinstance(item["quick_actions"], list)
