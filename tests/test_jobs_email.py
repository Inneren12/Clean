import asyncio
from datetime import date, datetime, timedelta, timezone

import sqlalchemy as sa

import uuid

from app.domain.bookings.db_models import Booking, EmailEvent
from app.domain.invoices import statuses as invoice_statuses
from app.domain.invoices.db_models import Invoice, Payment
from app.domain.leads.db_models import Lead
from app.domain.nps.db_models import NpsResponse
from app.infra.email import EmailAdapter
from app.jobs import email_jobs
from app.settings import settings


class StubAdapter(EmailAdapter):
    def __init__(self):
        super().__init__()
        self.sent: list[tuple[str, str, str]] = []

    async def send_email(
        self, recipient: str, subject: str, body: str, *, headers: dict[str, str] | None = None
    ) -> bool:  # type: ignore[override]
        self.sent.append((recipient, subject, body))
        return True


def _make_lead(name: str = "Customer") -> Lead:
    return Lead(
        name=name,
        phone="780-555-1234",
        email="customer@example.com",
        postal_code="T5A",
        address="1 Test St",
        preferred_dates=["Mon"],
        referral_code=uuid.uuid4().hex[:16],
        structured_inputs={"beds": 1, "baths": 1},
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


def test_booking_reminder_job_is_idempotent(async_session_maker):
    adapter = StubAdapter()
    original_base = settings.public_base_url
    settings.public_base_url = "https://example.test"

    async def _seed() -> None:
        async with async_session_maker() as session:
            lead = _make_lead()
            session.add(lead)
            await session.commit()
            await session.refresh(lead)
            booking = Booking(
                team_id=1,
                lead_id=lead.lead_id,
                starts_at=datetime.now(tz=timezone.utc) + timedelta(hours=12),
                duration_minutes=60,
                status="CONFIRMED",
            )
            session.add(booking)
            await session.commit()

    try:
        asyncio.run(_seed())

        async def _run() -> list[dict[str, int]]:
            results: list[dict[str, int]] = []
            async with async_session_maker() as session:
                results.append(await email_jobs.run_booking_reminders(session, adapter))
            async with async_session_maker() as session:
                results.append(await email_jobs.run_booking_reminders(session, adapter))
            return results

        first, second = asyncio.run(_run())
        assert first["sent"] == 1
        assert second["sent"] == 0
        assert len(adapter.sent) == 1

        async def _event_count() -> int:
            async with async_session_maker() as session:
                result = await session.execute(sa.select(sa.func.count()).select_from(EmailEvent))
                return int(result.scalar_one())

        assert asyncio.run(_event_count()) == 1
    finally:
        settings.public_base_url = original_base


def test_invoice_notifications_cover_sent_and_overdue(async_session_maker):
    adapter = StubAdapter()
    original_base = settings.public_base_url
    settings.public_base_url = "https://example.test"

    async def _seed_invoice() -> str:
        async with async_session_maker() as session:
            lead = _make_lead("Invoice Customer")
            session.add(lead)
            await session.commit()
            await session.refresh(lead)
            invoice = Invoice(
                invoice_number="INV-1001",
                order_id=None,
                customer_id=lead.lead_id,
                status=invoice_statuses.INVOICE_STATUS_SENT,
                issue_date=date.today(),
                due_date=date.today() + timedelta(days=1),
                currency="cad",
                subtotal_cents=10000,
                tax_cents=500,
                total_cents=10500,
            )
            session.add(invoice)
            await session.flush()
            payment = Payment(
                invoice_id=invoice.invoice_id,
                provider="stripe",
                method=invoice_statuses.PAYMENT_METHOD_CARD,
                amount_cents=5000,
                currency="cad",
                status=invoice_statuses.PAYMENT_STATUS_SUCCEEDED,
            )
            session.add(payment)
            await session.commit()
            return invoice.invoice_id

    try:
        invoice_id = asyncio.run(_seed_invoice())

        async def _run_jobs() -> tuple[dict[str, int], dict[str, int]]:
            async with async_session_maker() as session:
                first = await email_jobs.run_invoice_notifications(
                    session, adapter, base_url=settings.public_base_url
                )
            async with async_session_maker() as session:
                invoice = await session.get(Invoice, invoice_id)
                assert invoice
                invoice.due_date = date.today() - timedelta(days=1)
                await session.commit()
            async with async_session_maker() as session:
                second = await email_jobs.run_invoice_notifications(
                    session, adapter, base_url=settings.public_base_url
                )
            return first, second

        first_result, second_result = asyncio.run(_run_jobs())
        assert first_result == {"sent": 1, "overdue": 0}
        assert second_result == {"sent": 0, "overdue": 1}
        assert len(adapter.sent) == 2
        overdue_body = adapter.sent[-1][2]
        assert "Balance: CAD 55.00" in overdue_body

        async def _event_counts() -> tuple[int, int]:
            async with async_session_maker() as session:
                sent_count = await session.scalar(
                    sa.select(sa.func.count()).select_from(EmailEvent).where(EmailEvent.email_type == "invoice_sent")
                )
                overdue_count = await session.scalar(
                    sa.select(sa.func.count()).select_from(EmailEvent).where(EmailEvent.email_type == "invoice_overdue")
                )
                return int(sent_count or 0), int(overdue_count or 0)

        sent_events, overdue_events = asyncio.run(_event_counts())
        assert sent_events == 1
        assert overdue_events == 1
    finally:
        settings.public_base_url = original_base


def test_nps_job_skips_after_first_send(async_session_maker):
    adapter = StubAdapter()
    original_base = settings.public_base_url
    settings.public_base_url = "https://example.test"

    async def _seed_booking() -> str:
        async with async_session_maker() as session:
            lead = _make_lead("NPS Customer")
            session.add(lead)
            await session.commit()
            await session.refresh(lead)
            booking = Booking(
                team_id=1,
                lead_id=lead.lead_id,
                starts_at=datetime.now(tz=timezone.utc) - timedelta(days=1),
                duration_minutes=90,
                status="DONE",
            )
            session.add(booking)
            await session.commit()
            return booking.booking_id

    try:
        booking_id = asyncio.run(_seed_booking())

        async def _run_jobs() -> tuple[dict[str, int], dict[str, int]]:
            async with async_session_maker() as session:
                first = await email_jobs.run_nps_sends(session, adapter, base_url=settings.public_base_url)
            async with async_session_maker() as session:
                second = await email_jobs.run_nps_sends(session, adapter, base_url=settings.public_base_url)
            return first, second

        first_result, second_result = asyncio.run(_run_jobs())
        assert first_result == {"sent": 1}
        assert second_result == {"sent": 0}
        assert len(adapter.sent) == 1

        async def _event_and_response_counts() -> tuple[int, int]:
            async with async_session_maker() as session:
                event_count = await session.scalar(
                    sa.select(sa.func.count()).select_from(EmailEvent).where(EmailEvent.email_type == "nps_survey")
                )
                response_count = await session.scalar(
                    sa.select(sa.func.count()).select_from(NpsResponse).where(NpsResponse.order_id == booking_id)
                )
                return int(event_count or 0), int(response_count or 0)

        events, responses = asyncio.run(_event_and_response_counts())
        assert events == 1
        assert responses == 0
    finally:
        settings.public_base_url = original_base
