import base64
import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import sqlalchemy as sa

from app.domain.bookings.db_models import Booking
from app.domain.clients.db_models import ClientUser
from app.domain.leads.db_models import Lead
from app.domain.nps import service as nps_service
from app.domain.nps.db_models import NpsResponse, SupportTicket
from app.settings import settings


def _auth_headers(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _lead_payload(name: str = "Survey Lead", email: str = "survey@example.com") -> dict:
    return {
        "name": name,
        "phone": "780-555-9999",
        "email": email,
        "postal_code": "T5A",
        "address": "1 Test St",
        "preferred_dates": ["Mon"],
        "structured_inputs": {"beds": 1, "baths": 1, "cleaning_type": "standard"},
        "estimate_snapshot": {
            "price_cents": 12000,
            "subtotal_cents": 12000,
            "tax_cents": 0,
            "pricing_config_version": "v1",
            "config_hash": "hash",
            "line_items": [],
        },
        "pricing_config_version": "v1",
        "config_hash": "hash",
    }


def _seed_order(session_factory, order_id: str = "order-nps-1", *, email: str) -> tuple[str, str, str]:
    async def _create():
        async with session_factory() as session:
            client = ClientUser(email=email)
            session.add(client)
            await session.flush()

            lead = Lead(**_lead_payload(email=email))
            session.add(lead)
            await session.flush()

            booking = Booking(
                booking_id=order_id,
                client_id=client.client_id,
                lead_id=lead.lead_id,
                team_id=1,
                starts_at=datetime.now(timezone.utc),
                duration_minutes=60,
                planned_minutes=60,
                status="DONE",
                deposit_required=False,
                deposit_policy=[],
                consent_photos=False,
            )
            session.add(booking)
            await session.commit()
            return booking.booking_id, client.client_id, lead.lead_id

    return asyncio.run(_create())


@pytest.fixture()
def admin_credentials():
    original_username = settings.admin_basic_username
    original_password = settings.admin_basic_password
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"
    try:
        yield
    finally:
        settings.admin_basic_username = original_username
        settings.admin_basic_password = original_password


def test_nps_token_validation_and_single_response(client, async_session_maker):
    email = f"survey-{uuid4()}@example.com"
    order_id, client_id, _ = _seed_order(async_session_maker, "order-nps-1", email=email)
    token = nps_service.issue_nps_token(
        order_id,
        client_id=client_id,
        email=email,
        secret=settings.client_portal_secret,
    )

    bad = client.get(f"/nps/{order_id}?token=bad-token")
    assert bad.status_code == 400

    form = client.get(f"/nps/{order_id}?token={token}")
    assert form.status_code == 200
    assert "How did we do?" in form.text

    first = client.post(
        f"/nps/{order_id}",
        data={"token": token, "score": 8, "comment": "Great"},
    )
    assert first.status_code == 200

    repeat = client.post(
        f"/nps/{order_id}",
        data={"token": token, "score": 9},
    )
    assert repeat.status_code == 200
    assert "already received" in repeat.text

    async def _verify_response():
        async with async_session_maker() as session:
            result = await session.execute(
                sa.select(NpsResponse).where(NpsResponse.order_id == order_id)
            )
            return result.scalar_one()

    saved = asyncio.run(_verify_response())
    assert saved.score == 8
    assert saved.order_id == order_id


def test_low_score_creates_ticket_and_admin_api(client, async_session_maker, admin_credentials):
    email = f"survey-low-{uuid4()}@example.com"
    order_id, client_id, _ = _seed_order(async_session_maker, "order-nps-2", email=email)
    token = nps_service.issue_nps_token(
        order_id,
        client_id=client_id,
        email=email,
        secret=settings.client_portal_secret,
    )

    submission = client.post(
        f"/nps/{order_id}",
        data={"token": token, "score": 2, "comment": "Needs work"},
    )
    assert submission.status_code == 200

    async def _fetch_ticket():
        async with async_session_maker() as session:
            result = await session.execute(
                sa.select(SupportTicket).where(SupportTicket.order_id == order_id)
            )
            return result.scalar_one_or_none()

    ticket = asyncio.run(_fetch_ticket())
    assert ticket is not None
    assert ticket.status == "OPEN"

    list_resp = client.get(
        "/api/admin/tickets",
        headers=_auth_headers(settings.admin_basic_username, settings.admin_basic_password),
    )
    assert list_resp.status_code == 200
    body = list_resp.json()
    assert body["tickets"]
    first_ticket_id = body["tickets"][0]["id"]

    update = client.patch(
        f"/api/admin/tickets/{first_ticket_id}",
        json={"status": "IN_PROGRESS"},
        headers=_auth_headers(settings.admin_basic_username, settings.admin_basic_password),
    )
    assert update.status_code == 200
    assert update.json()["status"] == "IN_PROGRESS"


def test_admin_ticket_requires_auth(client, admin_credentials):
    response = client.get("/api/admin/tickets")
    assert response.status_code == 401
