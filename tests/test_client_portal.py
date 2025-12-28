from datetime import datetime, timedelta, timezone

import pytest

from app.domain.bookings.db_models import Booking
from app.domain.clients.db_models import ClientUser
from app.domain.clients.service import issue_magic_token, verify_magic_token
from app.main import app


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


def test_client_cannot_access_foreign_order(client):
    session_factory = app.state.db_session_factory

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

    import asyncio

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
