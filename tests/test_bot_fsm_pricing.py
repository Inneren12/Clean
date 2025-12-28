import anyio

from app.main import app


def _send_message(client, conversation_id: str, text: str):
    return client.post("/api/bot/message", json={"conversationId": conversation_id, "text": text}).json()


def test_price_single_message_flow(client):
    conversation_id = client.post("/api/bot/session", json={"channel": "web"}).json()["conversationId"]

    body = _send_message(
        client,
        conversation_id,
        "What's the price for a deep clean 2 bed 1 bath in Brooklyn tomorrow evening?",
    )

    reply = body["reply"]
    assert "Estimate" in reply["text"]
    assert reply["progress"]["total"] == 7
    assert reply["state"]["fsmStep"] == "ask_property_type"

    conversation = anyio.run(app.state.bot_store.get_conversation, conversation_id)
    assert conversation.state.last_estimate is not None
    assert conversation.state.last_estimate["priceRange"][0] < conversation.state.last_estimate["priceRange"][1]


def test_booking_step_by_step_reaches_confirm_lead(client):
    conversation_id = client.post("/api/bot/session", json={"channel": "web"}).json()["conversationId"]

    first = _send_message(client, conversation_id, "I want to book a cleaning")
    assert first["reply"]["state"]["fsmStep"] == "ask_service_type"

    second = _send_message(client, conversation_id, "Deep clean apartment")
    assert second["reply"]["state"]["fsmStep"] == "ask_size"

    third = _send_message(client, conversation_id, "2 bed 1 bath")
    assert third["reply"]["state"]["fsmStep"] == "ask_condition"

    fourth = _send_message(client, conversation_id, "It's pretty heavy")
    assert fourth["reply"]["state"]["fsmStep"] == "ask_extras"

    fifth = _send_message(client, conversation_id, "Add oven and windows")
    assert fifth["reply"]["state"]["fsmStep"] == "ask_area"

    sixth = _send_message(client, conversation_id, "In Manhattan")
    assert sixth["reply"]["state"]["fsmStep"] == "ask_preferred_time"

    seventh = _send_message(client, conversation_id, "Tomorrow at 9am works")
    assert seventh["reply"]["state"]["fsmStep"] == "ask_contact"

    final = _send_message(client, conversation_id, "Email me at test@example.com")
    assert final["reply"]["state"]["fsmStep"] == "confirm_lead"
    assert "confirm" in final["reply"]["text"].lower()

    conversation = anyio.run(app.state.bot_store.get_conversation, conversation_id)
    assert conversation.state.filled_fields.get("contact", {}).get("email") == "test@example.com"
    assert conversation.state.last_estimate is not None


def test_price_flow_skips_contact(client):
    conversation_id = client.post("/api/bot/session", json={"channel": "web"}).json()["conversationId"]

    reply = _send_message(client, conversation_id, "Price quote for studio apartment regular clean in Queens")
    assert reply["reply"]["state"]["fsmStep"] == "ask_condition"
    assert reply["reply"]["progress"]["total"] == 7
    assert "contact" not in reply["reply"]["quickReplies"]


def test_russian_price_flow(client):
    conversation_id = client.post("/api/bot/session", json={"channel": "web"}).json()["conversationId"]

    reply = _send_message(client, conversation_id, "Сколько стоит генеральная уборка квартиры?")
    assert reply["reply"]["intent"] == "price"
    assert reply["reply"]["progress"]["total"] == 7
    assert reply["reply"]["quickReplies"]


def test_skip_logic_when_entities_known(client):
    conversation_id = client.post("/api/bot/session", json={"channel": "web"}).json()["conversationId"]

    reply = _send_message(client, conversation_id, "Book deep clean for 3 bed house in Queens")
    assert reply["reply"]["state"]["fsmStep"] == "ask_condition"
    assert "ask_service_type" not in reply["reply"]["text"].lower()


def test_last_estimate_persists_across_turns(client):
    conversation_id = client.post("/api/bot/session", json={"channel": "web"}).json()["conversationId"]

    first = _send_message(client, conversation_id, "Need price for deep clean")
    first_estimate = first["reply"]["state"]["lastEstimate"]
    assert first_estimate is not None

    follow_up = _send_message(client, conversation_id, "It's a 1 bed apartment in Manhattan")
    assert follow_up["reply"]["state"]["lastEstimate"] is not None
    assert follow_up["reply"]["state"]["lastEstimate"] != first_estimate


def test_quick_replies_present_each_step(client):
    conversation_id = client.post("/api/bot/session", json={"channel": "web"}).json()["conversationId"]

    reply = _send_message(client, conversation_id, "I want to book")
    assert reply["reply"]["quickReplies"]
    next_reply = _send_message(client, conversation_id, "regular")
    assert next_reply["reply"]["quickReplies"]


def test_price_flow_works_in_single_step_and_stepwise(client):
    conversation_id = client.post("/api/bot/session", json={"channel": "web"}).json()["conversationId"]
    single = _send_message(client, conversation_id, "How much for a post-renovation clean 1200 sqft in Manhattan with windows?")
    assert "Estimate" in single["reply"]["text"]
    assert single["reply"]["state"]["fsmStep"] == "ask_property_type"

    conversation_id2 = client.post("/api/bot/session", json={"channel": "web"}).json()["conversationId"]
    step1 = _send_message(client, conversation_id2, "Need a price")
    assert step1["reply"]["state"]["fsmStep"] == "ask_service_type"
    step2 = _send_message(client, conversation_id2, "regular apartment")
    assert step2["reply"]["state"]["fsmStep"] == "ask_size"
