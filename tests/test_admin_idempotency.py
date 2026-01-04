import asyncio
from datetime import datetime, timezone
import base64
import uuid

import sqlalchemy as sa
from httpx import Response as HTTPXResponse, Request as HTTPXRequest

from app.domain.bookings.db_models import EmailEvent
from app.domain.export_events.db_models import ExportEvent
from app.domain.invoices import statuses as invoice_statuses
from app.domain.invoices.db_models import Invoice, Payment
from app.infra.security import InMemoryRateLimiter
from app.settings import settings


def _auth_headers(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _bootstrap_invoice(async_session_maker) -> str:
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"

    async def _create() -> str:
        async with async_session_maker() as session:
            invoice = Invoice(
                invoice_number=f"INV-{uuid.uuid4()}",
                status=invoice_statuses.INVOICE_STATUS_DRAFT,
                issue_date=datetime.now(tz=timezone.utc).date(),
                due_date=None,
                currency="CAD",
                subtotal_cents=1000,
                tax_cents=0,
                total_cents=1000,
                notes="manual",
                created_by="admin",
            )
            session.add(invoice)
            await session.commit()
            await session.refresh(invoice)
            return invoice.invoice_id

    return asyncio.run(_create())


def test_payment_idempotency_prevents_duplicate(client, async_session_maker):
    invoice_id = _bootstrap_invoice(async_session_maker)
    admin_headers = _auth_headers("admin", "secret")

    first = client.post(
        f"/v1/admin/invoices/{invoice_id}/record-payment",
        headers={**admin_headers, "Idempotency-Key": "pay-once"},
        json={"amount_cents": 500, "method": "cash"},
    )
    assert first.status_code == 201

    second = client.post(
        f"/v1/admin/invoices/{invoice_id}/record-payment",
        headers={**admin_headers, "Idempotency-Key": "pay-once"},
        json={"amount_cents": 500, "method": "cash"},
    )
    assert second.status_code == 201

    async def _count_payments() -> int:
        async with async_session_maker() as session:
            result = await session.execute(
                sa.select(sa.func.count()).select_from(Payment).where(Payment.invoice_id == invoice_id)
            )
            return int(result.scalar_one())

    payment_count = asyncio.run(_count_payments())
    assert payment_count == 1


def test_idempotency_conflict_returns_409(client, async_session_maker):
    invoice_id = _bootstrap_invoice(async_session_maker)
    admin_headers = _auth_headers("admin", "secret")

    first = client.post(
        f"/v1/admin/invoices/{invoice_id}/record-payment",
        headers={**admin_headers, "Idempotency-Key": "conflict-key"},
        json={"amount_cents": 500, "method": "cash"},
    )
    assert first.status_code == 201

    conflict = client.post(
        f"/v1/admin/invoices/{invoice_id}/record-payment",
        headers={**admin_headers, "Idempotency-Key": "conflict-key"},
        json={"amount_cents": 700, "method": "cash"},
    )
    assert conflict.status_code == 409


class RecordingAdapter:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    async def send_email(
        self, recipient: str, subject: str, body: str, headers: dict[str, str] | None = None
    ) -> bool:
        self.sent.append((recipient, subject, body))
        return True


def test_resend_email_event_idempotent(client, async_session_maker):
    settings.dispatcher_basic_username = "admin"
    settings.dispatcher_basic_password = "secret"
    client.app.state.email_adapter = RecordingAdapter()

    async def _seed_event() -> EmailEvent:
        async with async_session_maker() as session:
            event = EmailEvent(
                org_id=settings.default_org_id,
                email_type="booking_pending",
                recipient="idem@example.com",
                subject="Test",
                body="Hello",
                dedupe_key=f"test-{uuid.uuid4()}",
            )
            session.add(event)
            await session.commit()
            await session.refresh(event)
            return event

    event = asyncio.run(_seed_event())
    headers = _auth_headers("admin", "secret")

    first = client.post(
        f"/v1/admin/messaging/events/{event.event_id}/resend",
        headers={**headers, "Idempotency-Key": "resend-event"},
    )
    assert first.status_code == 202

    repeat = client.post(
        f"/v1/admin/messaging/events/{event.event_id}/resend",
        headers={**headers, "Idempotency-Key": "resend-event"},
    )
    assert repeat.status_code == 202
    assert len(client.app.state.email_adapter.sent) == 1


def test_replay_rate_limit_triggers(client, async_session_maker):
    original_limiter = client.app.state.action_rate_limiter
    client.app.state.action_rate_limiter = InMemoryRateLimiter(1)
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"

    async def _seed_event() -> ExportEvent:
        async with async_session_maker() as session:
            event = ExportEvent(
                org_id=settings.default_org_id,
                mode="webhook",
                target_url="https://example.com/webhook",
                payload={"demo": True},
                attempts=0,
            )
            session.add(event)
            await session.commit()
            await session.refresh(event)
            return event

    event = asyncio.run(_seed_event())
    client.app.state.export_transport = lambda request: HTTPXResponse(200, request=HTTPXRequest("POST", request.url))
    headers = {**_auth_headers("admin", "secret"), "Idempotency-Key": "replay-limit"}

    try:
        first = client.post(f"/v1/admin/export-dead-letter/{event.event_id}/replay", headers=headers)
        assert first.status_code == 202

        limited = client.post(f"/v1/admin/export-dead-letter/{event.event_id}/replay", headers=headers)
        assert limited.status_code == 429
    finally:
        client.app.state.action_rate_limiter = original_limiter
