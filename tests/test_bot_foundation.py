import anyio

from app.main import app


def test_create_conversation(client):
    response = client.post("/api/bot/session", json={"channel": "web", "anonId": "anon-1"})
    assert response.status_code == 201
    data = response.json()
    conversation_id = data["conversationId"]

    stored = anyio.run(app.state.bot_store.get_conversation, conversation_id)
    assert stored is not None
    assert stored.channel == "web"
    assert stored.anon_id == "anon-1"


def test_post_message_updates_state(client):
    conversation_id = client.post("/api/bot/session", json={"channel": "web"}).json()["conversationId"]

    response = client.post(
        "/api/bot/message",
        json={"conversationId": conversation_id, "text": "I need a price quote"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["reply"]["intent"] == "price"
    assert body["reply"]["state"]["fsmStep"] == "collecting_requirements"

    messages = anyio.run(app.state.bot_store.list_messages, conversation_id)
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[1].role == "bot"

    conversation = anyio.run(app.state.bot_store.get_conversation, conversation_id)
    assert conversation.state.filled_fields["last_message"] == "I need a price quote"


def test_message_normalizes_entities_into_state(client):
    conversation_id = client.post("/api/bot/session", json={"channel": "web"}).json()["conversationId"]

    client.post(
        "/api/bot/message",
        json={
            "conversationId": conversation_id,
            "text": "Please book a deep clean with oven and carpet tomorrow evening in Brooklyn",
        },
    )

    conversation = anyio.run(app.state.bot_store.get_conversation, conversation_id)
    filled = conversation.state.filled_fields
    assert filled["service_type"] == "deep_clean"
    assert set(filled.get("extras", [])) == {"oven", "carpet"}
    assert filled.get("area") == "Brooklyn"
    assert filled.get("preferred_time_window") == "tomorrow evening"


def test_create_lead_and_case(client):
    conversation_id = client.post("/api/bot/session", json={"channel": "web", "userId": "u-1"}).json()[
        "conversationId"
    ]

    client.post("/api/bot/message", json={"conversationId": conversation_id, "text": "Book a clean"})

    lead_response = client.post(
        "/api/leads",
        json={
            "service_type": "deep_clean",
            "contact": {"email": "test@example.com"},
            "sourceConversationId": conversation_id,
        },
    )
    assert lead_response.status_code == 201
    lead_body = lead_response.json()
    assert lead_body["leadId"]
    assert lead_body["sourceConversationId"] == conversation_id

    case_response = client.post(
        "/api/cases",
        json={
            "reason": "low_confidence",
            "summary": "handoff requested",
            "payload": {"note": "review manually"},
            "sourceConversationId": conversation_id,
        },
    )
    assert case_response.status_code == 201
    case_body = case_response.json()
    assert case_body["caseId"]
    assert case_body["reason"] == "low_confidence"
