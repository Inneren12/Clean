import anyio

from app.domain.bot.schemas import ConversationState
from app.main import app


def test_lead_merge_filters_extra_keys(client):
    conversation_id = client.post("/api/bot/session", json={"channel": "web"}).json()["conversationId"]
    anyio.run(
        app.state.bot_store.update_state,
        conversation_id,
        ConversationState(
            filled_fields={
                "service_type": "deep_clean",
                "last_message": "hello",
                "ignored_field": "value",
            }
        ),
    )

    response = client.post(
        "/api/leads",
        json={"contact": {"email": "merge@test.com"}, "sourceConversationId": conversation_id},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["serviceType"] == "deep_clean"
    assert "lastMessage" not in body
    assert "ignoredField" not in body


def test_payload_overrides_conversation_fields(client):
    conversation_id = client.post("/api/bot/session", json={"channel": "web"}).json()["conversationId"]
    anyio.run(
        app.state.bot_store.update_state,
        conversation_id,
        ConversationState(filled_fields={"service_type": "basic_clean"}),
    )

    response = client.post(
        "/api/leads",
        json={"serviceType": "premium_clean", "sourceConversationId": conversation_id},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["serviceType"] == "premium_clean"


def test_intents_use_enums_and_messages_list(client):
    conversation_id = client.post("/api/bot/session", json={"channel": "web"}).json()["conversationId"]

    response = client.post(
        "/api/bot/message", json={"conversationId": conversation_id, "text": "Tell me something"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["reply"]["intent"] == "faq"
    assert body["reply"]["state"]["fsmStep"] == "ask_service_type"
    assert body["reply"]["quickReplies"]  # FSM provides quick replies for flow

    session_response = client.get(f"/api/bot/session/{conversation_id}")
    assert session_response.status_code == 200
    session_state = session_response.json()["state"]
    assert session_state["currentIntent"] == "faq"

    messages_response = client.get("/api/bot/messages", params={"conversationId": conversation_id})
    assert messages_response.status_code == 200
    messages = messages_response.json()
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "bot"


def test_list_messages_accepts_legacy_conversation_id(client):
    conversation_id = client.post("/api/bot/session", json={"channel": "web"}).json()["conversationId"]
    response = client.get("/api/bot/messages", params={"conversation_id": conversation_id})
    assert response.status_code == 200


def test_rejects_unknown_request_fields(client):
    bad_session = client.post("/api/bot/session", json={"channel": "web", "unknown": "x"})
    assert bad_session.status_code == 422

    conversation_id = client.post("/api/bot/session", json={"channel": "web"}).json()["conversationId"]
    bad_message = client.post(
        "/api/bot/message",
        json={"conversationId": conversation_id, "text": "hi", "extra": "nope"},
    )
    assert bad_message.status_code == 422

    bad_lead = client.post("/api/leads", json={"serviceType": "deep_clean", "unknown": "x"})
    assert bad_lead.status_code == 422
