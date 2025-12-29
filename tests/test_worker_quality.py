import base64
import datetime
import io

import pytest
import sqlalchemy as sa
from fastapi import status

from app.domain.admin_audit.db_models import AdminAuditLog
from app.domain.bookings.db_models import Booking
from app.domain.disputes.db_models import Dispute
from app.settings import settings


def _basic_auth(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


async def _seed_booking(async_session_maker, *, team_id: int = 1, consent: bool = True) -> str:
    async with async_session_maker() as session:
        booking = Booking(
            team_id=team_id,
            starts_at=datetime.datetime.now(tz=datetime.timezone.utc),
            duration_minutes=60,
            status="PENDING",
            consent_photos=consent,
        )
        session.add(booking)
        await session.commit()
        await session.refresh(booking)
        return booking.booking_id


@pytest.mark.anyio
async def test_worker_checklist_photos_and_dispute_flow(client, async_session_maker):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"
    settings.worker_basic_username = "worker"
    settings.worker_basic_password = "secret"
    settings.worker_team_id = 1

    template_payload = {
        "name": "Standard",
        "items": [
            {"label": "Arrival photo", "phase": "BEFORE", "required": True},
            {"label": "Finish photo", "phase": "AFTER", "required": True},
        ],
    }
    template_resp = client.post(
        "/v1/admin/checklists/templates",
        json=template_payload,
        auth=("admin", "secret"),
    )
    assert template_resp.status_code == status.HTTP_201_CREATED

    booking_id = await _seed_booking(async_session_maker, consent=True)
    client.post("/worker/login", headers=_basic_auth("worker", "secret"))

    checklist = client.get(f"/worker/jobs/{booking_id}/checklist")
    assert checklist.status_code == status.HTTP_200_OK
    run = checklist.json()
    first_item = run["items"][0]["run_item_id"]
    second_item = run["items"][1]["run_item_id"]

    toggle_one = client.patch(
        f"/worker/jobs/{booking_id}/checklist/items/{first_item}",
        json={"checked": True},
    )
    assert toggle_one.status_code == status.HTTP_200_OK
    toggle_two = client.patch(
        f"/worker/jobs/{booking_id}/checklist/items/{second_item}",
        json={"checked": True},
    )
    assert toggle_two.status_code == status.HTTP_200_OK

    complete = client.post(f"/worker/jobs/{booking_id}/checklist/complete")
    assert complete.status_code == status.HTTP_200_OK
    assert complete.json()["status"] == "completed"

    upload = client.post(
        f"/worker/jobs/{booking_id}/photos",
        data={"phase": "before", "consent": True},
        files={"file": ("before.jpg", io.BytesIO(b"abc"), "image/jpeg")},
    )
    assert upload.status_code == status.HTTP_200_OK
    photo_id = upload.json()["photo_id"]

    photos = client.get(f"/worker/jobs/{booking_id}/photos")
    assert photos.status_code == status.HTTP_200_OK
    assert any(p["photo_id"] == photo_id for p in photos.json()["photos"])

    dispute = client.post(
        f"/worker/jobs/{booking_id}/disputes/report",
        json={"reason": "Damage spotted"},
    )
    assert dispute.status_code == status.HTTP_200_OK
    dispute_body = dispute.json()
    assert dispute_body["dispute_id"]
    assert "photo_refs" in dispute_body["facts"]

    async with async_session_maker() as session:
        stored_dispute = await session.get(Dispute, dispute_body["dispute_id"])
        assert stored_dispute is not None
        assert stored_dispute.facts_snapshot["photo_refs"] == [photo_id]
        audits = (await session.execute(sa.select(AdminAuditLog))).scalars().all()
        assert any(log.action == "WORKER_DISPUTE_REPORT" for log in audits)


@pytest.mark.anyio
async def test_worker_restricted_to_team_for_quality(client, async_session_maker):
    settings.worker_basic_username = "worker"
    settings.worker_basic_password = "secret"
    settings.worker_team_id = 1

    booking_id = await _seed_booking(async_session_maker, team_id=2, consent=True)
    client.post("/worker/login", headers=_basic_auth("worker", "secret"))

    assert client.get(f"/worker/jobs/{booking_id}/checklist").status_code == status.HTTP_404_NOT_FOUND
    assert (
        client.post(
            f"/worker/jobs/{booking_id}/photos",
            data={"phase": "before", "consent": True},
            files={"file": ("before.jpg", io.BytesIO(b"abc"), "image/jpeg")},
        ).status_code
        == status.HTTP_404_NOT_FOUND
    )
