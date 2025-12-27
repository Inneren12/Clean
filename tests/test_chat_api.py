import json

import anyio
from sqlalchemy import select

from app.domain.leads.db_models import ChatSession


def test_chat_turn_state_is_json_serializable(client):
    response = client.post(
        "/v1/chat/turn",
        json={"session_id": "test-session", "message": "2 bed 1 bath standard"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "state" in body
    json.dumps(body["state"])


def test_chat_turn_handles_bathroom_follow_up(client, async_session_maker):
    session_id = "loop-session"

    first = client.post(
        "/v1/chat/turn",
        json={"session_id": session_id, "message": "Need a deep clean 4 bed"},
    )
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["state"].get("awaiting_field") == "baths"
    assert any("bath" in question.lower() for question in first_body["proposed_questions"])

    second = client.post(
        "/v1/chat/turn",
        json={"session_id": session_id, "message": "2"},
    )
    assert second.status_code == 200
    second_body = second.json()

    assert second_body["state"].get("baths") == 2.0
    assert second_body["state"].get("awaiting_field") is None
    assert "baths" not in second_body["missing_fields"]

    async def fetch_state():
        async with async_session_maker() as session:
            result = await session.execute(
                select(ChatSession).where(ChatSession.session_id == session_id)
            )
            chat_session = result.scalar_one()
            return chat_session.state_json

    stored_state = anyio.run(fetch_state)
    assert stored_state.get("baths") == 2.0
    assert stored_state.get("awaiting_field") is None
