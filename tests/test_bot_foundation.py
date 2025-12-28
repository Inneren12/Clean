import anyio

from app.main import app


def test_create_conversation(client):
    response = client.post("/api/bot/session", json={"channel": "web", "anon_id": "anon-1"})
    assert response.status_code == 201
    data = response.json()
    conversation_id = data["conversation_id"]

    stored = anyio.run(app.state.bot_store.get_conversation, conversation_id)
    assert stored is not None
    assert stored.channel == "web"
    assert stored.anon_id == "anon-1"


def test_post_message_updates_state(client):
    conversation_id = client.post("/api/bot/session", json={"channel": "web"}).json()["conversation_id"]

    response = client.post(
        "/api/bot/message",
        json={"conversation_id": conversation_id, "text": "I need a price quote"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["reply"]["intent"] == "quote"
    assert body["reply"]["state"]["fsm_step"] == "collecting_requirements"

    messages = anyio.run(app.state.bot_store.list_messages, conversation_id)
    assert len(messages) == 2
    assert messages[0].role.value == "user"
    assert messages[1].role.value == "bot"

    conversation = anyio.run(app.state.bot_store.get_conversation, conversation_id)
    assert conversation.state.filled_fields["last_message"] == "I need a price quote"


def test_create_lead_and_case(client):
    conversation_id = client.post("/api/bot/session", json={"channel": "web", "user_id": "u-1"}).json()[
        "conversation_id"
    ]

    client.post("/api/bot/message", json={"conversation_id": conversation_id, "text": "Book a clean"})

    lead_response = client.post(
        "/api/leads",
        json={
            "service_type": "deep_clean",
            "contact": {"email": "test@example.com"},
            "source_conversation_id": conversation_id,
        },
    )
    assert lead_response.status_code == 201
    lead_body = lead_response.json()
    assert lead_body["lead_id"]
    assert lead_body["source_conversation_id"] == conversation_id

    case_response = client.post(
        "/api/cases",
        json={
            "reason": "low_confidence",
            "summary": "handoff requested",
            "payload": {"note": "review manually"},
            "source_conversation_id": conversation_id,
        },
    )
    assert case_response.status_code == 201
    case_body = case_response.json()
    assert case_body["case_id"]
    assert case_body["reason"] == "low_confidence"
