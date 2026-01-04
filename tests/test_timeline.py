"""Tests for unified timeline views."""

import asyncio
import base64
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.bookings.db_models import Booking
from app.domain.invoices.db_models import Invoice
from app.domain.leads.db_models import Lead

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


@pytest.fixture()
def sample_booking(async_session_maker):
    """Create a sample booking for timeline tests."""
    async def _create():
        async with async_session_maker() as session:
            lead = Lead(
                lead_id="test-lead-1",
                name="Test Customer",
                phone="123-456-7890",
                email="test@example.com",
                status="BOOKED",
                structured_inputs={},
                estimate_snapshot={},
                pricing_config_version=1,
                config_hash="test-hash",
            )
            session.add(lead)
            await session.flush()

            booking = Booking(
                booking_id="test-booking-1",
                team_id=1,
                lead_id=lead.lead_id,
                starts_at=datetime.now(timezone.utc) + timedelta(days=1),
                duration_minutes=120,
                status="CONFIRMED",
            )
            session.add(booking)
            await session.commit()
            return {"booking_id": booking.booking_id, "lead_id": lead.lead_id}

    return asyncio.run(_create())


@pytest.fixture()
def sample_invoice(async_session_maker):
    """Create a sample invoice for timeline tests."""
    async def _create():
        async with async_session_maker() as session:
            lead = Lead(
                lead_id="test-lead-2",
                name="Invoice Customer",
                phone="123-456-7891",
                email="invoice@example.com",
                status="BOOKED",
                structured_inputs={},
                estimate_snapshot={},
                pricing_config_version=1,
                config_hash="test-hash",
            )
            session.add(lead)
            await session.flush()

            invoice = Invoice(
                invoice_id="test-invoice-1",
                invoice_number="INV-2024-001",
                customer_id=lead.lead_id,
                status="SENT",
                issue_date=datetime.now(timezone.utc).date(),
                currency="CAD",
                subtotal_cents=10000,
                tax_cents=500,
                total_cents=10500,
            )
            session.add(invoice)
            await session.commit()
            return {"invoice_id": invoice.invoice_id, "lead_id": lead.lead_id}

    return asyncio.run(_create())


def test_booking_timeline(client, admin_credentials, sample_booking):
    """Test fetching booking timeline."""
    booking_id = sample_booking["booking_id"]
    response = client.get(
        f"/v1/admin/timeline/booking/{booking_id}",
        headers=_auth_headers("admin", "secret"),
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


def test_invoice_timeline(client, admin_credentials, sample_invoice):
    """Test fetching invoice timeline."""
    invoice_id = sample_invoice["invoice_id"]
    response = client.get(
        f"/v1/admin/timeline/invoice/{invoice_id}",
        headers=_auth_headers("admin", "secret"),
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


def test_timeline_event_structure(client, admin_credentials, sample_booking):
    """Test timeline events have expected structure."""
    booking_id = sample_booking["booking_id"]
    response = client.get(
        f"/v1/admin/timeline/booking/{booking_id}",
        headers=_auth_headers("admin", "secret"),
    )
    assert response.status_code == 200
    data = response.json()
    if data["events"]:
        event = data["events"][0]
        assert "event_id" in event
        assert "event_type" in event
        assert "timestamp" in event
        assert "action" in event


def test_timeline_chronological_order(client, admin_credentials, sample_booking):
    """Test timeline events are in chronological order (newest first)."""
    booking_id = sample_booking["booking_id"]
    response = client.get(
        f"/v1/admin/timeline/booking/{booking_id}",
        headers=_auth_headers("admin", "secret"),
    )
    assert response.status_code == 200
    data = response.json()
    if len(data["events"]) > 1:
        # Parse timestamps and verify ordering
        timestamps = [datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")) for e in data["events"]]
        for i in range(len(timestamps) - 1):
            assert timestamps[i] >= timestamps[i + 1], "Events should be sorted newest first"


def test_timeline_requires_auth(client):
    """Test that timeline endpoints require authentication."""
    response = client.get("/v1/admin/timeline/booking/fake-id")
    assert response.status_code == 401


def test_timeline_before_after_diffs(client, admin_credentials, sample_booking):
    """Test timeline includes before/after diffs for state changes."""
    booking_id = sample_booking["booking_id"]
    response = client.get(
        f"/v1/admin/timeline/booking/{booking_id}",
        headers=_auth_headers("admin", "secret"),
    )
    assert response.status_code == 200
    data = response.json()
    # Check if any audit log events include before/after
    audit_events = [e for e in data["events"] if e["event_type"] == "audit_log"]
    # Just verifying the structure supports it (not all events have diffs)
    for event in data["events"]:
        # before and after are optional fields
        if "before" in event:
            assert event["before"] is None or isinstance(event["before"], dict)
        if "after" in event:
            assert event["after"] is None or isinstance(event["after"], dict)
