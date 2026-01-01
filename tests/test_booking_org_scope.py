import base64
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.bookings.db_models import Booking
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


@pytest.mark.security
@pytest.mark.org_isolation
@pytest.mark.anyio
async def test_org_scoped_bookings(client, async_session_maker):
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()

    async with async_session_maker() as session:
        session.add_all(
            [
                Organization(org_id=org_a, name="Org A"),
                Organization(org_id=org_b, name="Org B"),
            ]
        )
        booking_a = Booking(
            org_id=org_a,
            team_id=1,
            starts_at=datetime.now(tz=timezone.utc) + timedelta(days=1),
            duration_minutes=60,
            status="PENDING",
        )
        booking_b = Booking(
            org_id=org_b,
            team_id=1,
            starts_at=datetime.now(tz=timezone.utc) + timedelta(days=2),
            duration_minutes=60,
            status="PENDING",
        )
        session.add_all([booking_a, booking_b])
        await session.commit()
        booking_a_id, booking_b_id = booking_a.booking_id, booking_b.booking_id

    headers = {
        **_auth_headers("admin", "secret"),
        "X-Test-Org": str(org_a),
    }

    today = datetime.now(tz=timezone.utc).date()
    list_resp = client.get(
        "/v1/admin/bookings",
        headers=headers,
        params={"from": today.isoformat(), "to": (today + timedelta(days=3)).isoformat()},
    )
    assert list_resp.status_code == 200
    booking_ids = {item["booking_id"] for item in list_resp.json()}
    assert booking_a_id in booking_ids
    assert booking_b_id not in booking_ids

    cancel_resp = client.post(
        f"/v1/admin/bookings/{booking_b_id}/cancel",
        headers=headers,
    )
    assert cancel_resp.status_code == 404

    reschedule_resp = client.post(
        f"/v1/admin/bookings/{booking_b_id}/reschedule",
        headers=headers,
        json={"starts_at": datetime.now(tz=timezone.utc).isoformat(), "time_on_site_hours": 1.5},
    )
    assert reschedule_resp.status_code == 404
