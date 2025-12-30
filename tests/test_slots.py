import asyncio
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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


def _parse_datetime(value: str) -> datetime:
    if value.endswith("Z"):
        value = value.replace("Z", "+00:00")
    return datetime.fromisoformat(value)


def test_slots_skip_booked_ranges(async_session_maker):
    async def _run() -> None:
        async with async_session_maker() as session:
            start_local = datetime(2025, 1, 1, 9, 0, tzinfo=ZoneInfo("America/Edmonton"))
            start_utc = start_local.astimezone(timezone.utc)
            await _insert_booking(session, start_utc, 60, status="CONFIRMED")
            slots = await generate_slots(date(2025, 1, 1), 60, session)
            assert start_utc not in slots
            expected_first_open = start_utc + timedelta(minutes=60 + BUFFER_MINUTES)
            assert expected_first_open in slots

    asyncio.run(_run())


def test_client_booking_api_blocks_slot(client):
    start = datetime(2025, 1, 1, 9, 0, tzinfo=ZoneInfo("America/Edmonton"))
    end = start + timedelta(hours=8)

    response = client.get(
        "/v1/client/slots",
        params={"from": start.isoformat(), "to": end.isoformat(), "duration_minutes": 120},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["slots"], "expected at least one slot"
    chosen_slot = data["slots"][0]

    create_resp = client.post(
        "/v1/client/bookings",
        json={"starts_at": chosen_slot, "duration_minutes": 120},
    )
    assert create_resp.status_code == 201

    follow_up = client.get(
        "/v1/client/slots",
        params={"from": start.isoformat(), "to": end.isoformat(), "duration_minutes": 120},
    )
    assert follow_up.status_code == 200
    next_slots = follow_up.json()["slots"]
    assert chosen_slot not in next_slots


def test_client_reschedule(client):
    start = datetime(2025, 1, 1, 9, 0, tzinfo=ZoneInfo("America/Edmonton"))
    end = start + timedelta(hours=8)
    slots_resp = client.get(
        "/v1/client/slots",
        params={"from": start.isoformat(), "to": end.isoformat(), "duration_minutes": 90},
    )
    assert slots_resp.status_code == 200
    slots = slots_resp.json()["slots"]
    assert len(slots) >= 2
    initial_slot, target_slot = slots[:2]

    booking_resp = client.post(
        "/v1/client/bookings",
        json={"starts_at": initial_slot, "duration_minutes": 90},
    )
    assert booking_resp.status_code == 201
    booking_id = booking_resp.json()["booking_id"]

    reschedule_resp = client.post(
        f"/v1/client/bookings/{booking_id}/reschedule",
        json={"starts_at": target_slot, "duration_minutes": 90},
    )
    assert reschedule_resp.status_code == 200
    assert _parse_datetime(reschedule_resp.json()["starts_at"]) == _parse_datetime(target_slot)



def test_round_duration_minutes_uses_slot_step():
    assert round_duration_minutes(1.1) == SLOT_STEP_MINUTES * 3  # 66 minutes => 90 rounded
    assert round_duration_minutes(0.1) == SLOT_STEP_MINUTES
    assert round_duration_minutes(2.5) == SLOT_STEP_MINUTES * 5
