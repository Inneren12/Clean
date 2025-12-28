from app.main import app


def _send_message(client, conversation_id: str, text: str):
    return client.post("/api/bot/message", json={"conversationId": conversation_id, "text": text}).json()


def test_fast_path_deep_clean_limits_questions_and_adds_upsell(client):
    conversation_id = client.post("/api/bot/session", json={"channel": "web"}).json()["conversationId"]

    reply = _send_message(client, conversation_id, "2 bed 2 bath deep clean with pets and windows")

    assert "Estimate" in reply["reply"]["text"]
    assert reply["reply"]["progress"]["total"] <= 4
    summary = reply["reply"].get("summary", {})
    assert set(summary.get("extras", [])) >= {"pets", "windows"}
    assert "Prep tips" in reply["reply"]["text"]


def test_prep_instructions_show_for_move_out(client):
    conversation_id = client.post("/api/bot/session", json={"channel": "web"}).json()["conversationId"]

    reply = _send_message(client, conversation_id, "Move-out cleaning 1 bed 1 bath in Queens")

    assert "prep" in reply["reply"]["text"].lower()
    assert reply["reply"]["progress"]["total"] <= 5
