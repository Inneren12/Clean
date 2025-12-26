import base64
import asyncio
from datetime import datetime, timedelta, timezone

from app.domain.leads.db_models import ChatSession, Lead
from app.domain.leads.statuses import LEAD_STATUS_BOOKED
from app.settings import settings


def _auth_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_retention_cleanup_counts_and_skips_leads_by_default(client, async_session_maker):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"

    estimate_response = client.post(
        "/v1/estimate",
        json={
            "beds": 1,
            "baths": 1,
            "cleaning_type": "standard",
            "heavy_grease": False,
            "multi_floor": False,
            "frequency": "one_time",
            "add_ons": {},
        },
    )
    assert estimate_response.status_code == 200
    lead_payload = {
        "name": "Retention Test",
        "phone": "555-555-0101",
        "preferred_dates": [],
        "structured_inputs": {"beds": 1, "baths": 1, "cleaning_type": "standard"},
        "estimate_snapshot": estimate_response.json(),
    }
    lead_response = client.post("/v1/leads", json=lead_payload)
    assert lead_response.status_code == 201
    lead_id = lead_response.json()["lead_id"]

    async def seed_old_records() -> None:
        async with async_session_maker() as session:
            chat = ChatSession(
                session_id="old-session",
                brand="economy",
                state_json={},
                updated_at=datetime.now(tz=timezone.utc) - timedelta(days=45),
            )
            lead = await session.get(Lead, lead_id)
            assert lead
            lead.created_at = datetime.now(tz=timezone.utc) - timedelta(days=400)
            lead.status = LEAD_STATUS_BOOKED
            session.add(chat)
            await session.commit()

    asyncio.run(seed_old_records())

    headers = _auth_header("admin", "secret")
    response = client.post("/v1/admin/retention/cleanup", headers=headers)
    assert response.status_code == 200
    assert response.json()["chat_sessions_deleted"] == 1
    assert response.json()["leads_deleted"] == 0

    async def verify_records() -> tuple[int, int]:
        async with async_session_maker() as session:
            remaining_chats = await session.get(ChatSession, "old-session")
            remaining_leads = await session.get(Lead, lead_id)
            return (1 if remaining_chats else 0, 1 if remaining_leads else 0)

    chats_left, leads_left = asyncio.run(verify_records())
    assert chats_left == 0
    assert leads_left == 1
