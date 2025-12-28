import asyncio
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

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
