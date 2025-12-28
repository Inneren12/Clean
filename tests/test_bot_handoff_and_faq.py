import anyio

from app.main import app


def _create_conversation(client) -> str:
    response = client.post("/api/bot/session", json={"channel": "web"})
    assert response.status_code == 201
    return response.json()["conversationId"]


def test_price_flow_captures_progress_and_summary(client):
    conversation_id = _create_conversation(client)

    response = client.post(
        "/api/bot/message",
        json={"conversationId": conversation_id, "text": "I need a price quote for a deep clean"},
    )

    assert response.status_code == 200
    reply = response.json()["reply"]
    assert reply["intent"] == "price"
    assert reply["progress"] is not None
    assert reply["quickReplies"]
    assert reply["summary"].get("service_type") == "deep_clean"


def test_booking_flow_tracks_entities_and_progress(client):
    conversation_id = _create_conversation(client)

    response = client.post(
        "/api/bot/message",
        json={
            "conversationId": conversation_id,
            "text": "Book cleaning for 2 bed 2 bath tomorrow evening in Brooklyn",
        },
    )

    assert response.status_code == 200
    reply = response.json()["reply"]
    assert reply["intent"] == "booking"
    assert reply["progress"] is not None
    assert reply["summary"].get("beds") == 2
    assert reply["summary"].get("baths") == 2
    assert reply["summary"].get("preferred_time_window") is not None


def test_faq_matches_top_answers_without_handoff(client):
    conversation_id = _create_conversation(client)

    response = client.post(
        "/api/bot/message",
        json={"conversationId": conversation_id, "text": "FAQ: what is included in a standard clean?"},
    )

    assert response.status_code == 200
    reply = response.json()["reply"]
    assert reply["intent"] in {"faq", "scope"}
    assert "Here’s what I found" in reply["text"]
    assert len(reply["quickReplies"]) >= 1
    cases = anyio.run(app.state.bot_store.list_cases)
    assert cases == []


def test_complaint_triggers_case_and_handoff(client):
    conversation_id = _create_conversation(client)

    response = client.post(
        "/api/bot/message", json={"conversationId": conversation_id, "text": "I have a complaint"}
    )

    assert response.status_code == 200
    reply = response.json()["reply"]
    assert reply["intent"] == "complaint"
    assert reply["quickReplies"] == []
    cases = anyio.run(app.state.bot_store.list_cases)
    assert len(cases) == 1
    assert cases[0].reason == "complaint"
    assert cases[0].payload.get("messages")


def test_low_confidence_message_hands_off(client):
    conversation_id = _create_conversation(client)

    response = client.post(
        "/api/bot/message",
        json={"conversationId": conversation_id, "text": "???"},
    )

    assert response.status_code == 200
    reply = response.json()["reply"]
    assert reply["quickReplies"] == []
    cases = anyio.run(app.state.bot_store.list_cases)
    assert len(cases) == 1
    assert cases[0].reason == "low_confidence"
    assert cases[0].payload.get("entities") == {"extras": []}
