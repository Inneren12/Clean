from datetime import datetime, timezone

import pytest

from app.settings import settings


def _create_booking(client, starts_at: datetime) -> str:
    response = client.post(
        "/v1/bookings",
        json={"starts_at": starts_at.isoformat(), "time_on_site_hours": 1.0},
    )
    assert response.status_code == 201
    return response.json()["booking_id"]


def _admin_auth():
    return settings.admin_basic_username, settings.admin_basic_password


@pytest.fixture(autouse=True)
def _configure_admin():
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"


@pytest.fixture()
def template_payload():
    return {
        "name": "Standard",
        "items": [
            {"label": "Lock doors", "phase": "BEFORE", "required": True},
            {"label": "Photos", "phase": "AFTER", "required": True},
        ],
    }


def test_required_items_validation(client, template_payload):
    template_resp = client.post(
        "/v1/admin/checklists/templates",
        json=template_payload,
        auth=_admin_auth(),
    )
    assert template_resp.status_code == 201

    booking_id = _create_booking(client, datetime(2024, 7, 1, 15, 0, tzinfo=timezone.utc))

    init_resp = client.post(
        f"/v1/orders/{booking_id}/checklist/init",
        json={},
        auth=_admin_auth(),
    )
    assert init_resp.status_code == 201
    run = init_resp.json()

    before_item = next(item for item in run["items"] if item["phase"] == "BEFORE")
    after_item = next(item for item in run["items"] if item["phase"] == "AFTER")

    toggle_resp = client.patch(
        f"/v1/orders/{booking_id}/checklist/items/{before_item['run_item_id']}",
        json={"checked": True},
        auth=_admin_auth(),
    )
    assert toggle_resp.status_code == 200

    complete_resp = client.post(
        f"/v1/orders/{booking_id}/checklist/complete",
        auth=_admin_auth(),
    )
    assert complete_resp.status_code == 400
    assert "Required items" in complete_resp.json()["detail"]

    client.patch(
        f"/v1/orders/{booking_id}/checklist/items/{after_item['run_item_id']}",
        json={"checked": True},
        auth=_admin_auth(),
    )

    final_resp = client.post(
        f"/v1/orders/{booking_id}/checklist/complete",
        auth=_admin_auth(),
    )
    assert final_resp.status_code == 200
    assert final_resp.json()["status"] == "completed"


def test_template_version_selection(client):
    payload = {
        "name": "Deep clean",
        "service_type": "deep_clean",
        "items": [
            {"label": "Prep", "phase": "BEFORE", "required": False},
        ],
    }
    first = client.post(
        "/v1/admin/checklists/templates",
        json=payload,
        auth=_admin_auth(),
    )
    assert first.status_code == 201
    second = client.post(
        "/v1/admin/checklists/templates",
        json=payload,
        auth=_admin_auth(),
    )
    assert second.status_code == 201
    latest_template = second.json()

    booking_one = _create_booking(client, datetime(2024, 7, 2, 15, 0, tzinfo=timezone.utc))
    init_one = client.post(
        f"/v1/orders/{booking_one}/checklist/init",
        json={"service_type": "deep_clean"},
        auth=_admin_auth(),
    )
    assert init_one.status_code == 201
    assert init_one.json()["template_version"] == latest_template["version"]

    deactivate = client.put(
        f"/v1/admin/checklists/templates/{latest_template['template_id']}",
        json={"is_active": False},
        auth=_admin_auth(),
    )
    assert deactivate.status_code == 200

    booking_two = _create_booking(client, datetime(2024, 7, 3, 16, 0, tzinfo=timezone.utc))
    init_two = client.post(
        f"/v1/orders/{booking_two}/checklist/init",
        json={"service_type": "deep_clean"},
        auth=_admin_auth(),
    )
    assert init_two.status_code == 201
    assert init_two.json()["template_version"] == 1


def test_update_used_template_creates_new_version(client):
    """Regression test: updating template items after it has been used should not cause FK breakage."""
    # Create template with initial items
    template_payload = {
        "name": "Move checklist",
        "service_type": "move",
        "items": [
            {"label": "Check inventory", "phase": "BEFORE", "required": True},
            {"label": "Load truck", "phase": "BEFORE", "required": True},
        ],
    }
    create_resp = client.post(
        "/v1/admin/checklists/templates",
        json=template_payload,
        auth=_admin_auth(),
    )
    assert create_resp.status_code == 201
    original_template = create_resp.json()
    original_template_id = original_template["template_id"]
    original_version = original_template["version"]

    # Create booking and init checklist (this creates a run referencing the template)
    booking_id = _create_booking(client, datetime(2024, 7, 10, 15, 0, tzinfo=timezone.utc))
    init_resp = client.post(
        f"/v1/orders/{booking_id}/checklist/init",
        json={"service_type": "move"},
        auth=_admin_auth(),
    )
    assert init_resp.status_code == 201
    run = init_resp.json()
    assert run["template_id"] == original_template_id
    assert run["template_version"] == original_version

    # Now update the template items - this should create a NEW version instead of mutating
    update_payload = {
        "items": [
            {"label": "Check inventory v2", "phase": "BEFORE", "required": True},
            {"label": "Load truck v2", "phase": "BEFORE", "required": False},
            {"label": "Secure items", "phase": "AFTER", "required": True},
        ],
    }
    update_resp = client.put(
        f"/v1/admin/checklists/templates/{original_template_id}",
        json=update_payload,
        auth=_admin_auth(),
    )
    assert update_resp.status_code == 200
    new_template = update_resp.json()

    # Verify new version was created
    assert new_template["template_id"] != original_template_id
    assert new_template["version"] > original_version
    assert len(new_template["items"]) == 3
    assert new_template["items"][0]["label"] == "Check inventory v2"

    # Verify old template was deactivated
    templates_resp = client.get(
        "/v1/admin/checklists/templates",
        auth=_admin_auth(),
    )
    assert templates_resp.status_code == 200
    templates = templates_resp.json()
    old_template = next(t for t in templates if t["template_id"] == original_template_id)
    assert old_template["is_active"] is False

    # CRITICAL: Verify existing run still works and references old template/items correctly
    get_checklist_resp = client.get(
        f"/v1/orders/{booking_id}/checklist",
        auth=_admin_auth(),
    )
    assert get_checklist_resp.status_code == 200
    checklist = get_checklist_resp.json()
    assert checklist["template_id"] == original_template_id
    assert checklist["template_version"] == original_version
    assert len(checklist["items"]) == 2  # Still has original 2 items
    assert checklist["items"][0]["label"] == "Check inventory"
    assert checklist["items"][1]["label"] == "Load truck"
