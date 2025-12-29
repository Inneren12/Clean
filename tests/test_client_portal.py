import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from app.domain.bookings.db_models import Booking
from app.domain.bookings.policy import (
    BookingPolicySnapshot,
    CancellationPolicySnapshot,
    CancellationWindow,
    DepositSnapshot,
)
from app.domain.bookings.service import DepositDecision
from app.domain.clients.db_models import ClientUser
from app.domain.clients.service import issue_magic_token, verify_magic_token
from app.domain.leads.db_models import Lead
from app.main import app

LOCAL_TZ = ZoneInfo("America/Edmonton")


def test_magic_link_expiry():
    secret = "test-secret"
    issued = datetime.now(timezone.utc) - timedelta(minutes=40)
    token = issue_magic_token(
        "alice@example.com",
        "client-1",
        secret=secret,
        ttl_minutes=15,
        issued_at=issued,
    )

    with pytest.raises(ValueError):
        verify_magic_token(token, secret=secret)


def test_client_cannot_access_foreign_order(client, async_session_maker):
    session_factory = async_session_maker

    async def seed_data():
        async with session_factory() as session:
            c1 = ClientUser(email="c1@example.com")
            c2 = ClientUser(email="c2@example.com")
            session.add_all([c1, c2])
            await session.flush()

            b1 = Booking(
                booking_id="order-1",
                client_id=c1.client_id,
                team_id=1,
                starts_at=datetime.now(timezone.utc),
                duration_minutes=60,
                planned_minutes=60,
                status="PENDING",
                deposit_required=False,
                deposit_policy=[],
                consent_photos=False,
            )
            b2 = Booking(
                booking_id="order-2",
                client_id=c2.client_id,
                team_id=1,
                starts_at=datetime.now(timezone.utc),
                duration_minutes=60,
                planned_minutes=60,
                status="PENDING",
                deposit_required=False,
                deposit_policy=[],
                consent_photos=False,
            )
            session.add_all([b1, b2])
            await session.commit()
            return c1.client_id, c2.client_id

    c1_id, _ = asyncio.run(seed_data())

    token = issue_magic_token(
        "c1@example.com",
        c1_id,
        secret=app.state.app_settings.client_portal_secret
        if hasattr(app.state, "app_settings")
        else "dev-client-portal-secret",
        ttl_minutes=30,
    )
    client.cookies.set("client_session", token)

    forbidden = client.get("/client/orders/order-2")
    assert forbidden.status_code == 404

    allowed = client.get("/client/orders/order-1")
    assert allowed.status_code == 200


def test_repeat_order_reevaluates_deposit_policy(client, async_session_maker, monkeypatch):
    """
    Regression test for repeat_order deposit bypass.

    Ensures that when repeating an order, deposit rules are re-evaluated
    for the new booking date rather than bypassed with deposit_decision=None.
    """
    session_factory = async_session_maker

    async def seed_data():
        async with session_factory() as session:
            # Create a client user
            client_user = ClientUser(email="test@example.com")
            session.add(client_user)
            await session.flush()

            # Create a lead with estimate for deposit calculation
            lead = Lead(
                name="Test Lead",
                phone="780-555-0000",
                email="test@example.com",
                postal_code="T5K",
                preferred_dates=["Mon"],
                structured_inputs={"beds": 2, "baths": 1, "cleaning_type": "standard"},
                estimate_snapshot={
                    "pricing_config_version": "v1",
                    "config_hash": "hash",
                    "total_before_tax": 150.0,
                },
                pricing_config_version="v1",
                config_hash="hash",
                status="NEW",
            )
            session.add(lead)
            await session.flush()

            # Create an initial booking without deposit
            # Use a valid booking time: tomorrow at 10:00 AM Edmonton time
            now_local = datetime.now(tz=LOCAL_TZ)
            # Find the next weekday
            days_ahead = 1
            while (now_local + timedelta(days=days_ahead)).weekday() >= 5:
                days_ahead += 1
            booking_start_local = (now_local + timedelta(days=days_ahead)).replace(
                hour=10, minute=0, second=0, microsecond=0
            )
            booking_start_utc = booking_start_local.astimezone(timezone.utc)

            booking = Booking(
                booking_id="original-order",
                client_id=client_user.client_id,
                lead_id=lead.lead_id,
                team_id=1,
                starts_at=booking_start_utc,
                duration_minutes=120,
                planned_minutes=120,
                status="PENDING",
                deposit_required=False,
                deposit_policy=[],
                deposit_status=None,
                consent_photos=False,
            )
            session.add(booking)
            await session.commit()
            return client_user.client_id

    client_id = asyncio.run(seed_data())

    # Monkeypatch evaluate_deposit_policy to return a decision requiring deposit
    # This simulates the case where the new booking date triggers deposit rules
    # (e.g., weekend, heavy cleaning, or new client policies)
    mock_policy_snapshot = BookingPolicySnapshot(
        lead_time_hours=48.0,
        service_type=None,
        total_amount_cents=15000,
        first_time_client=True,
        deposit=DepositSnapshot(
            required=True,
            amount_cents=3750,
            percent_applied=0.25,
            min_cents=5000,
            max_cents=20000,
            reasons=["weekend"],
            basis="percent_clamped",
        ),
        cancellation=CancellationPolicySnapshot(
            rules=[],
            windows=[
                CancellationWindow(
                    label="free", start_hours_before=24.0, end_hours_before=None, refund_percent=100
                )
            ],
        ),
    )
    mock_deposit_decision = DepositDecision(
        required=True,
        reasons=["weekend"],
        deposit_cents=3750,  # 25% of $150 = $37.50
        policy_snapshot=mock_policy_snapshot,
    )

    async def mock_evaluate_deposit_policy(*args, **kwargs):
        return mock_deposit_decision

    from app.domain.bookings import service as booking_service

    monkeypatch.setattr(
        booking_service, "evaluate_deposit_policy", mock_evaluate_deposit_policy
    )

    # Authenticate as the client
    token = issue_magic_token(
        "test@example.com",
        client_id,
        secret=app.state.app_settings.client_portal_secret
        if hasattr(app.state, "app_settings")
        else "dev-client-portal-secret",
        ttl_minutes=30,
    )
    client.cookies.set("client_session", token)

    # Call repeat_order endpoint
    response = client.post("/client/orders/original-order/repeat")
    assert response.status_code == 201, response.text

    data = response.json()

    # Verify the new booking was created
    assert data["order_id"] != "original-order"
    assert data["status"] == "PENDING"
    assert data["duration_minutes"] == 120

    # Verify deposit information is stored in the database
    async def verify_deposit():
        async with session_factory() as session:
            result = await session.execute(
                sa.select(Booking).where(Booking.booking_id == data["order_id"])
            )
            repeated_booking = result.scalar_one()

            # CRITICAL: The repeated booking must have deposit_required=True
            # This proves that deposit rules were re-evaluated, not bypassed
            assert repeated_booking.deposit_required is True
            assert repeated_booking.deposit_cents == 3750
            assert "weekend" in repeated_booking.deposit_policy
            assert repeated_booking.deposit_status == "pending"

    import sqlalchemy as sa
    asyncio.run(verify_deposit())
