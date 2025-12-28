import anyio

from app.bot.nlu.models import Intent
from app.domain.bot.schemas import FsmStep
from app.main import app


def _send_message(client, conversation_id: str, text: str):
    return client.post("/api/bot/message", json={"conversationId": conversation_id, "text": text}).json()


def _list_cases():
    return anyio.run(app.state.bot_store.list_cases)


def test_complaint_creates_case(client):
    conversation_id = client.post("/api/bot/session", json={"channel": "web"}).json()["conversationId"]

    response = _send_message(client, conversation_id, "I have a complaint about service")
    assert response["reply"]["intent"] == Intent.complaint.value
    assert response["reply"]["state"]["fsmStep"] == FsmStep.handoff_check.value

    cases = _list_cases()
    assert len(cases) == 1
    assert cases[0].reason == "complaint"
    assert cases[0].source_conversation_id == conversation_id


def test_human_request_creates_case(client):
    conversation_id = client.post("/api/bot/session", json={"channel": "web"}).json()["conversationId"]

    response = _send_message(client, conversation_id, "Can I talk to a human?")
    assert response["reply"]["intent"] == Intent.human.value
    assert response["reply"]["state"]["fsmStep"] == FsmStep.handoff_check.value

    cases = _list_cases()
    assert len(cases) == 1
    assert cases[0].reason == "human_requested"


def test_low_confidence_short_message_clarifies(client):
    conversation_id = client.post("/api/bot/session", json={"channel": "web"}).json()["conversationId"]

    response = _send_message(client, conversation_id, "ok")
    assert "which topic fits best" in response["reply"]["text"].lower()
    assert response["reply"]["state"]["fsmStep"] == FsmStep.routing.value
    assert _list_cases() == []


def test_faq_match_returns_answer_without_case(client):
    conversation_id = client.post("/api/bot/session", json={"channel": "web"}).json()["conversationId"]

    response = _send_message(client, conversation_id, "faq: pricing details")
    assert "here's what i found" in response["reply"]["text"].lower()
    assert _list_cases() == []


def test_faq_no_match_clarifies(client):
    conversation_id = client.post("/api/bot/session", json={"channel": "web"}).json()["conversationId"]

    response = _send_message(client, conversation_id, "faq: xyz???")
    assert "which topic fits best" in response["reply"]["text"].lower()
    assert _list_cases() == []


def test_status_does_not_trigger_handoff(client):
    conversation_id = client.post("/api/bot/session", json={"channel": "web"}).json()["conversationId"]

    booking = _send_message(client, conversation_id, "Book a clean")
    assert booking["reply"]["state"]["fsmStep"] == FsmStep.ask_service_type.value

    status = _send_message(client, conversation_id, "Status update")
    assert status["reply"]["state"]["fsmStep"] == FsmStep.ask_service_type.value
    assert status["reply"]["quickReplies"]

    resume = _send_message(client, conversation_id, "Deep clean apartment")
    assert resume["reply"]["state"]["fsmStep"] == FsmStep.ask_size.value
    assert _list_cases() == []
