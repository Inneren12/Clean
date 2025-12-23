def test_estimate_api_success(client):
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
    assert body["pricing_config_id"] == "economy"
    assert body["pricing_config_version"] == "v1"
    assert body["config_hash"].startswith("sha256:")
    assert body["team_size"] > 0
    assert body["labor_cost"] > 0
    assert body["total_before_tax"] > 0


def test_estimate_api_validation_error(client):
    response = client.post("/v1/estimate", json={"beds": -1, "baths": 1})
    assert response.status_code == 422
    body = response.json()
    assert body["title"] == "Validation Error"
    assert body["type"].endswith("validation-error")
    assert body["request_id"]
    assert body["errors"]


def test_estimate_api_invalid_add_on_key(client):
    response = client.post(
        "/v1/estimate",
        json={
            "beds": 2,
            "baths": 1,
            "cleaning_type": "standard",
            "add_ons": {"not_real": True},
        },
    )
    assert response.status_code == 422
    body = response.json()
    assert body["errors"]
    assert any(error["field"] == "add_ons.not_real" for error in body["errors"])
