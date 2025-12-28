import asyncio
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock

from app.domain.bookings.db_models import Booking
from app.domain.bookings.service import StubSlotProvider, TimeWindowPreference, apply_duration_constraints, suggest_slots
from app.domain.pricing.models import CleaningType


async def _insert_booking(session, starts_at: datetime, duration_minutes: int) -> Booking:
    booking = Booking(
        team_id=1,
        starts_at=starts_at,
        duration_minutes=duration_minutes,
        status="CONFIRMED",
    )
    session.add(booking)
    await session.commit()
    await session.refresh(booking)
    return booking


def test_stub_provider_limits_and_fills(async_session_maker):
    async def _run() -> None:
        provider = StubSlotProvider()
        async with async_session_maker() as session:
            # Block the morning window so fallback logic is exercised
            start_local = datetime(2025, 1, 1, 9, 0, tzinfo=ZoneInfo("America/Edmonton"))
            await _insert_booking(session, start_local.astimezone(timezone.utc), 60)

            result = await suggest_slots(
                date(2025, 1, 1),
                60,
                session,
                time_window=TimeWindowPreference(start_hour=9, end_hour=10),
                team_id=1,
                provider=provider,
            )

            assert result.slots, "expected fallback suggestions when window is blocked"
            assert len(result.slots) <= provider.max_suggestions
            assert result.clarifier

    asyncio.run(_run())


def test_apply_duration_constraints():
    assert apply_duration_constraints(30, CleaningType.standard) == 60
    assert apply_duration_constraints(1000, CleaningType.standard) == 240
    assert apply_duration_constraints(1000, None) == 540


def test_time_window_bounds_allows_24():
    window = TimeWindowPreference(start_hour=9, end_hour=24)
    target_date = date(2025, 1, 1)

    start, end = window.bounds(target_date)

    expected_start = datetime(2025, 1, 1, 9, 0, tzinfo=ZoneInfo("America/Edmonton")).astimezone(
        timezone.utc
    )
    expected_end = datetime(2025, 1, 2, 0, 0, tzinfo=ZoneInfo("America/Edmonton")).astimezone(
        timezone.utc
    )

    assert start == expected_start
    assert end == expected_end


def test_window_filter_respects_duration_and_date():
    provider = StubSlotProvider()
    target_date = date(2025, 1, 1)
    time_window = TimeWindowPreference(start_hour=9, end_hour=10)
    slot_local = datetime(2025, 1, 1, 9, 0, tzinfo=ZoneInfo("America/Edmonton"))
    slots = [slot_local.astimezone(timezone.utc)]

    filtered = provider._filter_by_window(slots, time_window, target_date, 90)

    assert filtered == []


def test_filter_uses_target_date_when_no_slots():
    provider = StubSlotProvider()
    expected_date = date(2030, 1, 1)

    mock_window = MagicMock(spec=TimeWindowPreference)

    def _bounds(target_date: date):
        assert target_date == expected_date
        start = datetime.combine(expected_date, time(hour=9, tzinfo=ZoneInfo("America/Edmonton")))
        end = datetime.combine(expected_date, time(hour=10, tzinfo=ZoneInfo("America/Edmonton")))
        return start.astimezone(timezone.utc), end.astimezone(timezone.utc)

    mock_window.bounds.side_effect = _bounds

    filtered = provider._filter_by_window([], mock_window, expected_date, 60)

    assert filtered == []
