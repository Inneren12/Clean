import asyncio

from sqlalchemy import select

from app.domain.leads.db_models import ChatSession, Lead


def test_chat_to_lead_flow(client, async_session_maker):
    session_id = "session-e2e"
    first = client.post(
        "/v1/chat/turn",
        json={"session_id": session_id, "message": "Need a deep clean"},
    )
    assert first.status_code == 200

    second = client.post(
        "/v1/chat/turn",
        json={"session_id": session_id, "message": "2 bed 2 bath with oven"},
    )
    assert second.status_code == 200
    body = second.json()
    estimate = body["estimate"]
    assert estimate is not None

    lead_payload = {
        "name": "Alex Booker",
        "phone": "780-555-3333",
        "preferred_dates": ["Fri evening", "Sat morning"],
        "structured_inputs": body["state"],
        "estimate_snapshot": estimate,
    }
    response = client.post("/v1/leads", json=lead_payload)
    assert response.status_code == 201
    lead_id = response.json()["lead_id"]

    async def fetch_records():
        async with async_session_maker() as session:
            lead_result = await session.execute(select(Lead).where(Lead.lead_id == lead_id))
            chat_result = await session.execute(
                select(ChatSession).where(ChatSession.session_id == session_id)
            )
            return lead_result.scalar_one(), chat_result.scalar_one()

    lead, chat_session = asyncio.run(fetch_records())
    assert lead.estimate_snapshot["pricing_config_version"] == estimate["pricing_config_version"]
    assert chat_session.state_json["beds"] == 2
