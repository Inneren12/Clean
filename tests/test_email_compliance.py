import asyncio
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from app.domain.bookings.db_models import Booking, EmailEvent
from app.domain.leads.db_models import Lead
from app.domain.notifications import email_service
from app.domain.notifications.db_models import EmailFailure
from app.infra.email import EmailAdapter
from app.settings import settings


class StubAdapter(EmailAdapter):
    def __init__(self):
        super().__init__()
        self.sent: list[tuple[str, str, str]] = []

    async def send_email(  # type: ignore[override]
        self, recipient: str, subject: str, body: str, *, headers: dict[str, str] | None = None
    ) -> bool:
        self.sent.append((recipient, subject, body))
        return True


class FailingAdapter(EmailAdapter):
    async def send_email(  # type: ignore[override]
        self, recipient: str, subject: str, body: str, *, headers: dict[str, str] | None = None
    ) -> bool:
        raise RuntimeError("forced_failure")


async def _make_booking(session_factory, *, starts_at: datetime) -> Booking:
    async with session_factory() as session:
        lead = Lead(
            name="Reminder Lead",
            phone="780-555-1234",
            email="customer@example.com",
            postal_code="T5A",
            address="1 Test St",
            preferred_dates=["Mon"],
            structured_inputs={"beds": 1, "baths": 1, "cleaning_type": "standard"},
            estimate_snapshot={
                "price_cents": 10000,
                "subtotal_cents": 10000,
                "tax_cents": 0,
                "pricing_config_version": "v1",
                "config_hash": "hash",
                "line_items": [],
            },
            pricing_config_version="v1",
            config_hash="hash",
        )
        session.add(lead)
        await session.flush()
        booking = Booking(
            team_id=1,
            lead_id=lead.lead_id,
            starts_at=starts_at,
            duration_minutes=60,
            status="CONFIRMED",
        )
        session.add(booking)
        await session.commit()
        await session.refresh(booking)
        return booking


def test_concurrent_scans_do_not_duplicate(async_session_maker):
    adapter = StubAdapter()
    asyncio.run(
        _make_booking(async_session_maker, starts_at=datetime.now(tz=timezone.utc) + timedelta(hours=12))
    )

    async def _run_scan() -> dict[str, int]:
        async with async_session_maker() as session:
            return await email_service.scan_and_send_reminders(session, adapter)

    asyncio.run(asyncio.gather(_run_scan(), _run_scan()))

    async def _count_events() -> int:
        async with async_session_maker() as session:
            result = await session.execute(sa.select(sa.func.count()).select_from(EmailEvent))
            return int(result.scalar_one())

    assert asyncio.run(_count_events()) == 1
    assert len(adapter.sent) == 1


def test_dlq_retry_and_dead_letter(async_session_maker):
    adapter = FailingAdapter()
    booking = asyncio.run(
        _make_booking(async_session_maker, starts_at=datetime.now(tz=timezone.utc) + timedelta(hours=1))
    )

    async def _attempt_send() -> None:
        async with async_session_maker() as session:
            await session.refresh(booking)
            lead = await session.get(Lead, booking.lead_id)
            await email_service.send_booking_confirmed_email(session, adapter, booking, lead)

    asyncio.run(_attempt_send())

    async def _inspect_failure():
        async with async_session_maker() as session:
            failure = await session.scalar(sa.select(EmailFailure))
            assert failure is not None
            failure.next_retry_at = datetime.now(tz=timezone.utc)
            await session.commit()
            return failure.failure_id

    failure_id = asyncio.run(_inspect_failure())

    success_adapter = StubAdapter()

    async def _retry():
        async with async_session_maker() as session:
            result = await email_service.retry_email_failures(session, success_adapter)
            updated = await session.get(EmailFailure, failure_id)
            return result, updated

    retry_result, updated_failure = asyncio.run(_retry())
    assert retry_result["sent"] == 1
    assert updated_failure.status == "sent"


def test_unsubscribe_blocks_marketing(async_session_maker):
    adapter = StubAdapter()
    booking = asyncio.run(
        _make_booking(async_session_maker, starts_at=datetime.now(tz=timezone.utc) + timedelta(days=1))
    )
    original_base = settings.public_base_url
    settings.public_base_url = "https://app.test"

    async def _unsubscribe_and_send():
        async with async_session_maker() as session:
            await session.refresh(booking)
            lead = await session.get(Lead, booking.lead_id)
            await email_service.register_unsubscribe(
                session, recipient=lead.email, scope=email_service.SCOPE_MARKETING, org_id=booking.org_id
            )
            await email_service.send_booking_completed_email(session, adapter, booking, lead)
            await email_service.send_booking_confirmed_email(session, adapter, booking, lead)
            return lead.email

    recipient = asyncio.run(_unsubscribe_and_send())
    settings.public_base_url = original_base
    assert len(adapter.sent) == 1
    assert adapter.sent[0][0] == recipient


def test_templates_use_configured_urls(async_session_maker):
    adapter = StubAdapter()
    booking = asyncio.run(
        _make_booking(async_session_maker, starts_at=datetime.now(tz=timezone.utc) + timedelta(hours=2))
    )
    original_base = settings.public_base_url
    settings.public_base_url = "https://example.test"

    async def _send():
        async with async_session_maker() as session:
            lead = await session.get(Lead, booking.lead_id)
            await email_service.send_booking_completed_email(session, adapter, booking, lead)
            await session.refresh(booking)
            result = await session.execute(sa.select(EmailEvent).order_by(EmailEvent.created_at.desc()))
            return result.scalars().first()

    event = asyncio.run(_send())
    settings.public_base_url = original_base
    assert "https://example.test" in event.body
