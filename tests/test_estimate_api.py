from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_estimate_api_success():
    response = client.post(
        "/v1/estimate",
        json={
            "beds": 2,
            "baths": 1,
            "cleaning_type": "standard",
            "heavy_grease": False,
            "multi_floor": False,
            "frequency": "one_time",
            "add_ons": {},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["pricing_config_id"] == "economy_v1"
    assert "config_hash" in body


def test_estimate_api_validation_error():
    response = client.post("/v1/estimate", json={"beds": -1, "baths": 1})
    assert response.status_code == 422
    body = response.json()
    assert body["title"] == "Validation Error"
