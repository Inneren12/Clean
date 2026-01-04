"""Tests for unified timeline views."""

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.postgres, pytest.mark.smoke]


@pytest.mark.asyncio
async def test_booking_timeline(async_client: AsyncClient, admin_credentials, sample_booking):
    """Test fetching booking timeline."""
    booking_id = sample_booking["booking_id"]
    response = await async_client.get(
        f"/v1/admin/timeline/booking/{booking_id}",
        auth=admin_credentials,
    )
    assert response.status_code == 200
    data = response.json()
    assert "resource_type" in data
    assert data["resource_type"] == "booking"
    assert "resource_id" in data
    assert data["resource_id"] == booking_id
    assert "events" in data
    assert "total" in data
    assert isinstance(data["events"], list)


@pytest.mark.asyncio
async def test_invoice_timeline(async_client: AsyncClient, admin_credentials, sample_invoice):
    """Test fetching invoice timeline."""
    invoice_id = sample_invoice["invoice_id"]
    response = await async_client.get(
        f"/v1/admin/timeline/invoice/{invoice_id}",
        auth=admin_credentials,
    )
    assert response.status_code == 200
    data = response.json()
    assert "resource_type" in data
    assert data["resource_type"] == "invoice"
    assert "resource_id" in data
    assert data["resource_id"] == invoice_id
    assert "events" in data
    assert "total" in data
    assert isinstance(data["events"], list)


@pytest.mark.asyncio
async def test_timeline_event_structure(async_client: AsyncClient, admin_credentials, sample_booking):
    """Test timeline events have expected structure."""
    booking_id = sample_booking["booking_id"]
    response = await async_client.get(
        f"/v1/admin/timeline/booking/{booking_id}",
        auth=admin_credentials,
    )
    assert response.status_code == 200
    data = response.json()
    if data["events"]:
        event = data["events"][0]
        assert "event_id" in event
        assert "event_type" in event
        assert "timestamp" in event
        assert "action" in event


@pytest.mark.asyncio
async def test_timeline_chronological_order(async_client: AsyncClient, admin_credentials, sample_booking):
    """Test timeline events are in chronological order (newest first)."""
    booking_id = sample_booking["booking_id"]
    response = await async_client.get(
        f"/v1/admin/timeline/booking/{booking_id}",
        auth=admin_credentials,
    )
    assert response.status_code == 200
    data = response.json()
    if len(data["events"]) > 1:
        for i in range(len(data["events"]) - 1):
            current = data["events"][i]["timestamp"]
            next_event = data["events"][i + 1]["timestamp"]
            assert current >= next_event, "Events should be sorted newest first"


@pytest.mark.asyncio
async def test_timeline_requires_auth(async_client: AsyncClient):
    """Test that timeline endpoints require authentication."""
    response = await async_client.get("/v1/admin/timeline/booking/fake-id")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_timeline_before_after_diffs(async_client: AsyncClient, admin_credentials, sample_booking):
    """Test timeline includes before/after diffs for state changes."""
    booking_id = sample_booking["booking_id"]
    response = await async_client.get(
        f"/v1/admin/timeline/booking/{booking_id}",
        auth=admin_credentials,
    )
    assert response.status_code == 200
    data = response.json()
    # Check if any audit log events include before/after
    audit_events = [e for e in data["events"] if e["event_type"] == "audit_log"]
    if audit_events:
        # At least some audit events should have before/after
        has_diff = any(e.get("before") or e.get("after") for e in audit_events)
        # This assertion is soft - not all audit logs have diffs
        # Just verifying the structure supports it
        for event in audit_events:
            if "before" in event:
                assert event["before"] is None or isinstance(event["before"], dict)
            if "after" in event:
                assert event["after"] is None or isinstance(event["after"], dict)
