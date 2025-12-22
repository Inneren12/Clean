from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_leads_api_not_found_in_sprint_1():
    response = client.post(
        "/v1/leads",
        json={
            "session_id": "session-123",
            "contact_name": "Jane Doe",
            "email": "jane@example.com",
            "phone": "780-555-1234",
            "notes": "Call after 5pm",
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Not Found"
