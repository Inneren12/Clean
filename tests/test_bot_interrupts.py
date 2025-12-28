import anyio

from app.bot.nlu.models import Intent
from app.domain.bot.schemas import FsmStep
from app.main import app


def _send_message(client, conversation_id: str, text: str):
    return client.post("/api/bot/message", json={"conversationId": conversation_id, "text": text}).json()


def test_human_interrupts_booking_flow(client):
    conversation_id = client.post("/api/bot/session", json={"channel": "web"}).json()["conversationId"]

    initial = _send_message(client, conversation_id, "I want to book a cleaning")
    assert initial["reply"]["state"]["fsmStep"] == FsmStep.ask_service_type.value

    interrupt = _send_message(client, conversation_id, "I need a human now")
    assert interrupt["reply"]["intent"] == Intent.human.value
    assert interrupt["reply"]["state"]["fsmStep"] == FsmStep.handoff_check.value

    conversation = anyio.run(app.state.bot_store.get_conversation, conversation_id)
    assert conversation.state.current_intent == Intent.human
    assert conversation.state.fsm_step == FsmStep.handoff_check


def test_complaint_interrupts_price_flow(client):
    conversation_id = client.post("/api/bot/session", json={"channel": "web"}).json()["conversationId"]

    _send_message(client, conversation_id, "Need a price quote")
    complaint = _send_message(client, conversation_id, "I have a complaint about the last cleaning")

    assert complaint["reply"]["intent"] == Intent.complaint.value
    assert complaint["reply"]["state"]["fsmStep"] == FsmStep.handoff_check.value

    conversation = anyio.run(app.state.bot_store.get_conversation, conversation_id)
    assert conversation.state.current_intent == Intent.complaint


def test_status_does_not_derail_flow(client):
    conversation_id = client.post("/api/bot/session", json={"channel": "web"}).json()["conversationId"]

    booking = _send_message(client, conversation_id, "Book a clean")
    assert booking["reply"]["state"]["fsmStep"] == FsmStep.ask_service_type.value

    status = _send_message(client, conversation_id, "Status update please")
    assert status["reply"]["intent"] == Intent.status.value
    assert status["reply"]["state"]["fsmStep"] == FsmStep.ask_service_type.value

    resume = _send_message(client, conversation_id, "Deep clean apartment")
    assert resume["reply"]["state"]["fsmStep"] == FsmStep.ask_size.value

    conversation = anyio.run(app.state.bot_store.get_conversation, conversation_id)
    assert conversation.state.current_intent == Intent.booking
