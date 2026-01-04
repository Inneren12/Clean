"""Tests for operator work queues."""

import pytest
from datetime import datetime, timedelta, timezone
from httpx import AsyncClient

pytestmark = [pytest.mark.postgres, pytest.mark.smoke]


@pytest.mark.asyncio
async def test_photo_queue_pending(async_client: AsyncClient, admin_credentials):
    """Test fetching pending photos queue."""
    response = await async_client.get(
        "/v1/admin/queue/photos?status=pending",
        auth=admin_credentials,
    )
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert "pending_count" in data
    assert "needs_retake_count" in data
    assert isinstance(data["items"], list)


@pytest.mark.asyncio
async def test_photo_queue_needs_retake(async_client: AsyncClient, admin_credentials):
    """Test fetching photos needing retake."""
    response = await async_client.get(
        "/v1/admin/queue/photos?status=needs_retake",
        auth=admin_credentials,
    )
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "needs_retake_count" in data


@pytest.mark.asyncio
async def test_invoice_queue_overdue(async_client: AsyncClient, admin_credentials):
    """Test fetching overdue invoices queue."""
    response = await async_client.get(
        "/v1/admin/queue/invoices?status=overdue",
        auth=admin_credentials,
    )
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert "overdue_count" in data
    assert "unpaid_count" in data
    assert isinstance(data["items"], list)


@pytest.mark.asyncio
async def test_invoice_queue_unpaid(async_client: AsyncClient, admin_credentials):
    """Test fetching unpaid invoices queue."""
    response = await async_client.get(
        "/v1/admin/queue/invoices?status=unpaid",
        auth=admin_credentials,
    )
    assert response.status_code == 200
    data = response.json()
    assert "unpaid_count" in data


@pytest.mark.asyncio
async def test_assignment_queue(async_client: AsyncClient, admin_credentials):
    """Test fetching unassigned bookings queue."""
    response = await async_client.get(
        "/v1/admin/queue/assignments?days_ahead=7",
        auth=admin_credentials,
    )
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert "urgent_count" in data
    assert isinstance(data["items"], list)


@pytest.mark.asyncio
async def test_assignment_queue_custom_window(async_client: AsyncClient, admin_credentials):
    """Test assignment queue with custom look-ahead window."""
    response = await async_client.get(
        "/v1/admin/queue/assignments?days_ahead=14",
        auth=admin_credentials,
    )
    assert response.status_code == 200
    data = response.json()
    assert "urgent_count" in data


@pytest.mark.asyncio
async def test_dlq_all(async_client: AsyncClient, admin_credentials):
    """Test fetching all dead letter queue items."""
    response = await async_client.get(
        "/v1/admin/queue/dlq?kind=all",
        auth=admin_credentials,
    )
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert "outbox_dead_count" in data
    assert "export_dead_count" in data
    assert isinstance(data["items"], list)


@pytest.mark.asyncio
async def test_dlq_outbox_only(async_client: AsyncClient, admin_credentials):
    """Test fetching only outbox dead letters."""
    response = await async_client.get(
        "/v1/admin/queue/dlq?kind=outbox",
        auth=admin_credentials,
    )
    assert response.status_code == 200
    data = response.json()
    assert "outbox_dead_count" in data


@pytest.mark.asyncio
async def test_dlq_export_only(async_client: AsyncClient, admin_credentials):
    """Test fetching only export dead letters."""
    response = await async_client.get(
        "/v1/admin/queue/dlq?kind=export",
        auth=admin_credentials,
    )
    assert response.status_code == 200
    data = response.json()
    assert "export_dead_count" in data


@pytest.mark.asyncio
async def test_queue_pagination(async_client: AsyncClient, admin_credentials):
    """Test queue pagination works correctly."""
    response = await async_client.get(
        "/v1/admin/queue/photos?limit=10&offset=0",
        auth=admin_credentials,
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) <= 10


@pytest.mark.asyncio
async def test_queue_requires_auth(async_client: AsyncClient):
    """Test that queue endpoints require authentication."""
    response = await async_client.get("/v1/admin/queue/photos")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_queue_quick_actions(async_client: AsyncClient, admin_credentials):
    """Test that queue items include quick actions."""
    response = await async_client.get(
        "/v1/admin/queue/photos?limit=1",
        auth=admin_credentials,
    )
    assert response.status_code == 200
    data = response.json()
    if data["items"]:
        item = data["items"][0]
        assert "quick_actions" in item
        assert isinstance(item["quick_actions"], list)
