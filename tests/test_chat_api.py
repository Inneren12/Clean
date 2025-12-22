import json


def test_chat_turn_state_is_json_serializable(client):
    response = client.post(
        "/v1/chat/turn",
        json={"session_id": "test-session", "message": "2 bed 1 bath standard"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "state" in body
    json.dumps(body["state"])
