import asyncio
import base64
import uuid

import pytest

from app.domain.saas.db_models import Organization
from app.settings import settings


def _auth_headers(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture(autouse=True)
def admin_credentials():
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"
    settings.dispatcher_basic_username = "admin"
    settings.dispatcher_basic_password = "secret"
    yield


def _lead_payload(client, *, name: str = "Lead") -> dict:
    estimate_response = client.post(
        "/v1/estimate",
        json={
            "beds": 1,
            "baths": 1,
            "cleaning_type": "standard",
            "heavy_grease": False,
            "multi_floor": False,
            "frequency": "one_time",
            "add_ons": {},
        },
    )
    assert estimate_response.status_code == 200
    return {
        "name": name,
        "phone": "780-555-0101",
        "preferred_dates": [],
        "structured_inputs": {"beds": 1, "baths": 1, "cleaning_type": "standard"},
        "estimate_snapshot": estimate_response.json(),
    }


def _create_lead(client, *, name: str, org_id: uuid.UUID) -> str:
    headers = {"X-Test-Org": str(org_id)}
    response = client.post("/v1/leads", json=_lead_payload(client, name=name), headers=headers)
    assert response.status_code == 201
    return response.json()["lead_id"]


def test_leads_are_org_scoped(monkeypatch, client, async_session_maker):
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()

    def _resolve_org_id(request):
        header_value = request.headers.get("X-Test-Org")
        return uuid.UUID(header_value) if header_value else settings.default_org_id

    monkeypatch.setattr("app.api.entitlements.resolve_org_id", _resolve_org_id)

    async def _seed_orgs() -> None:
        async with async_session_maker() as session:
            session.add_all(
                [
                    Organization(org_id=org_a, name="Org A"),
                    Organization(org_id=org_b, name="Org B"),
                ]
            )
            await session.commit()

    asyncio.run(_seed_orgs())

    lead_a = _create_lead(client, name="Org A Lead", org_id=org_a)
    lead_b = _create_lead(client, name="Org B Lead", org_id=org_b)

    admin_headers_a = {**_auth_headers("admin", "secret"), "X-Test-Org": str(org_a)}
    admin_headers_b = {**_auth_headers("admin", "secret"), "X-Test-Org": str(org_b)}

    list_a = client.get("/v1/admin/leads", headers=admin_headers_a)
    assert list_a.status_code == 200
    lead_ids_a = {item["lead_id"] for item in list_a.json()}
    assert lead_a in lead_ids_a
    assert lead_b not in lead_ids_a

    list_b = client.get("/v1/admin/leads", headers=admin_headers_b)
    assert list_b.status_code == 200
    lead_ids_b = {item["lead_id"] for item in list_b.json()}
    assert lead_b in lead_ids_b
    assert lead_a not in lead_ids_b

    update_other_org = client.post(
        f"/v1/admin/leads/{lead_b}/status",
        headers=admin_headers_a,
        json={"status": "CONTACTED"},
    )
    assert update_other_org.status_code == 404

    update_same_org = client.post(
        f"/v1/admin/leads/{lead_a}/status",
        headers=admin_headers_a,
        json={"status": "CONTACTED"},
    )
    assert update_same_org.status_code == 200
    assert update_same_org.json()["status"] == "CONTACTED"
