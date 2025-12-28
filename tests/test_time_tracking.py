from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.domain.bookings.db_models import Booking
from app.domain.time_tracking.db_models import WorkTimeEntry
from app.domain.time_tracking import service as time_service
from app.settings import settings


def _new_booking(start: datetime) -> Booking:
    return Booking(
        team_id=1,
        lead_id=None,
        starts_at=start,
        duration_minutes=60,
        planned_minutes=5,
        status="PENDING",
        deposit_required=False,
        deposit_policy=[],
        deposit_status=None,
    )


@pytest.mark.anyio
async def test_time_tracking_state_machine(async_session_maker):
    async with async_session_maker() as session:
        start = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
        booking = _new_booking(start)
        session.add(booking)
        await session.commit()
        await session.refresh(booking)

        pause_at = start + timedelta(minutes=2)
        resume_at = pause_at + timedelta(minutes=1)
        finish_at = resume_at + timedelta(minutes=3)

        entry = await time_service.start_time_tracking(
            session, booking.booking_id, worker_id="worker-1", now=start
        )
        assert entry.state == time_service.RUNNING

        entry = await time_service.pause_time_tracking(session, booking.booking_id, now=pause_at)
        assert entry.total_seconds == 120
        assert entry.state == time_service.PAUSED

        entry = await time_service.resume_time_tracking(session, booking.booking_id, now=resume_at)
        assert entry.state == time_service.RUNNING

        entry = await time_service.finish_time_tracking(session, booking.booking_id, now=finish_at)
        assert entry.state == time_service.FINISHED
        assert entry.total_seconds == 300

        summary = await time_service.fetch_time_tracking_summary(
            session, booking.booking_id, now=finish_at
        )
        assert summary["effective_seconds"] == 300
        assert summary["planned_seconds"] == 300
        assert summary["leak_flag"] is False

        refreshed = await session.get(Booking, booking.booking_id)
        assert refreshed.actual_seconds == 300
        assert refreshed.actual_duration_minutes == 5


@pytest.mark.anyio
async def test_time_tracking_start_idempotent(async_session_maker):
    async with async_session_maker() as session:
        start = datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc)
        booking = _new_booking(start)
        session.add(booking)
        await session.commit()
        await session.refresh(booking)

        first = await time_service.start_time_tracking(session, booking.booking_id, now=start)
        second = await time_service.start_time_tracking(
            session, booking.booking_id, now=start + timedelta(seconds=10)
        )

        stmt = select(WorkTimeEntry).where(WorkTimeEntry.booking_id == booking.booking_id)
        result = await session.execute(stmt)
        entries = result.scalars().all()
        assert len(entries) == 1
        assert entries[0].entry_id == first.entry_id == second.entry_id


def test_time_tracking_endpoints(client):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"

    start = datetime(2024, 5, 1, 15, 0, tzinfo=timezone.utc).isoformat()
    response = client.post(
        "/v1/bookings",
        json={"starts_at": start, "time_on_site_hours": 1.5},
    )
    assert response.status_code == 201
    booking_id = response.json()["booking_id"]

    auth = (settings.admin_basic_username, settings.admin_basic_password)
    start_resp = client.post(f"/v1/orders/{booking_id}/time/start", auth=auth)
    assert start_resp.status_code == 200

    pause_resp = client.post(f"/v1/orders/{booking_id}/time/pause", auth=auth)
    assert pause_resp.status_code == 200

    finish_resp = client.post(f"/v1/orders/{booking_id}/time/finish", auth=auth)
    assert finish_resp.status_code == 200

    summary_resp = client.get(f"/v1/orders/{booking_id}/time", auth=auth)
    assert summary_resp.status_code == 200
    body = summary_resp.json()
    assert body["booking_id"] == booking_id
    assert body["planned_seconds"] == body["planned_minutes"] * 60

    admin_resp = client.get("/v1/admin/orders/time", auth=auth)
    assert admin_resp.status_code == 200
    bookings = {item["booking_id"] for item in admin_resp.json()}
    assert booking_id in bookings
