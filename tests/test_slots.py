import asyncio
from datetime import date, datetime, timedelta, timezone

from app.domain.bookings.db_models import Booking
from app.domain.bookings.service import BUFFER_MINUTES, SLOT_STEP_MINUTES, generate_slots, round_duration_minutes


async def _insert_booking(session, starts_at: datetime, duration_minutes: int, status: str = "CONFIRMED") -> Booking:
    booking = Booking(
        team_id=1,
        starts_at=starts_at,
        duration_minutes=duration_minutes,
        status=status,
    )
    session.add(booking)
    await session.commit()
    await session.refresh(booking)
    return booking


def test_slots_skip_booked_ranges(async_session_maker):
    async def _run() -> None:
        async with async_session_maker() as session:
            start = datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc)
            await _insert_booking(session, start, 60, status="CONFIRMED")
            slots = await generate_slots(date(2025, 1, 1), 60, session)
            assert start not in slots
            expected_first_open = start + timedelta(minutes=60 + BUFFER_MINUTES)
            assert expected_first_open in slots

    asyncio.run(_run())


def test_booking_api_blocks_slot(client):
    response = client.get("/v1/slots", params={"date": "2025-01-01", "time_on_site_hours": 2.0})
    assert response.status_code == 200
    data = response.json()
    assert data["slots"], "expected at least one slot"
    chosen_slot = data["slots"][0]

    create_resp = client.post(
        "/v1/bookings",
        json={"starts_at": chosen_slot, "time_on_site_hours": 2.0},
    )
    assert create_resp.status_code == 201

    follow_up = client.get("/v1/slots", params={"date": "2025-01-01", "time_on_site_hours": 2.0})
    assert follow_up.status_code == 200
    next_slots = follow_up.json()["slots"]
    assert chosen_slot not in next_slots



def test_round_duration_minutes_uses_slot_step():
    assert round_duration_minutes(1.1) == SLOT_STEP_MINUTES * 3  # 66 minutes => 90 rounded
    assert round_duration_minutes(0.1) == SLOT_STEP_MINUTES
    assert round_duration_minutes(2.5) == SLOT_STEP_MINUTES * 5
