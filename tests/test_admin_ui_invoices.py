import asyncio
import base64
from datetime import date, datetime, timezone

from app.domain.bookings.db_models import Booking
from app.domain.invoices import service as invoice_service
from app.domain.invoices.schemas import InvoiceItemCreate
from app.settings import settings


def _basic_auth(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_admin_invoice_ui_list_and_detail(client, async_session_maker):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"

    async def seed_invoice() -> str:
        async with async_session_maker() as session:
            booking = Booking(
                team_id=1,
                lead_id=None,
                starts_at=datetime.now(tz=timezone.utc),
                duration_minutes=60,
                status="PENDING",
            )
            session.add(booking)
            await session.flush()

            invoice = await invoice_service.create_invoice_from_order(
                session=session,
                order=booking,
                items=[InvoiceItemCreate(description="UI Seed", qty=1, unit_price_cents=15000)],
                issue_date=date.today(),
                due_date=date.today(),
                currency="CAD",
                notes="Seed invoice for UI",
                created_by="admin",
            )
            await session.commit()
            return invoice.invoice_id

    invoice_id = asyncio.run(seed_invoice())

    headers = _basic_auth("admin", "secret")
    list_response = client.get("/v1/admin/ui/invoices", headers=headers)
    assert list_response.status_code == 200
    assert "Invoices" in list_response.text

    detail_response = client.get(f"/v1/admin/ui/invoices/{invoice_id}", headers=headers)
    assert detail_response.status_code == 200
    assert invoice_id in detail_response.text
    assert "Record manual payment" in detail_response.text
