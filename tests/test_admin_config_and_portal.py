import asyncio
import asyncio
import base64
from datetime import date, datetime, timedelta, timezone

import sqlalchemy as sa

from app.domain.bookings.db_models import Booking
from app.domain.clients.db_models import ClientUser
from app.domain.clients.service import issue_magic_token
from app.domain.invoices import statuses as invoice_statuses
from app.domain.invoices.db_models import Invoice, Payment
from app.domain.leads.db_models import Lead
from app.domain.nps.db_models import SupportTicket
from app.domain.subscriptions import (
    schemas as subscription_schemas,
    service as subscription_service,
    statuses as subscription_statuses,
)
from app.domain.subscriptions.db_models import Subscription
from app.settings import settings


def _auth_headers(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_client_invoice_listing_scoped(client, async_session_maker):
    async def seed_data() -> tuple[str, str]:
        async with async_session_maker() as session:
            client_one = ClientUser(email="client1@example.com")
            client_two = ClientUser(email="client2@example.com")
            lead = Lead(
                name="Lead One",
                phone="000",
                email="client1@example.com",
                postal_code="00000",
                preferred_dates=["Mon"],
                structured_inputs=None,
                estimate_snapshot=None,
                pricing_config_version="v1",
                config_hash="hash",
                status="NEW",
            )
            session.add_all([client_one, client_two, lead])
            await session.flush()

            booking_one = Booking(
                booking_id="order-one",
                client_id=client_one.client_id,
                team_id=1,
                starts_at=datetime.now(timezone.utc),
                duration_minutes=60,
                planned_minutes=60,
                status="PENDING",
                deposit_required=False,
                deposit_policy=[],
                consent_photos=False,
            )
            booking_two = Booking(
                booking_id="order-two",
                client_id=client_two.client_id,
                team_id=1,
                starts_at=datetime.now(timezone.utc),
                duration_minutes=60,
                planned_minutes=60,
                status="PENDING",
                deposit_required=False,
                deposit_policy=[],
                consent_photos=False,
            )
            session.add_all([booking_one, booking_two])
            await session.flush()

            invoice_one = Invoice(
                invoice_id="inv-one",
                invoice_number="INV-1",
                order_id=booking_one.booking_id,
                customer_id=None,
                status="OPEN",
                issue_date=date.today(),
                due_date=date.today() + timedelta(days=7),
                currency="usd",
                subtotal_cents=10000,
                tax_cents=0,
                total_cents=10000,
                notes=None,
                created_by=None,
            )
            invoice_two = Invoice(
                invoice_id="inv-two",
                invoice_number="INV-2",
                order_id=booking_two.booking_id,
                customer_id=None,
                status="OPEN",
                issue_date=date.today(),
                due_date=None,
                currency="usd",
                subtotal_cents=20000,
                tax_cents=0,
                total_cents=20000,
                notes=None,
                created_by=None,
            )
            invoice_three = Invoice(
                invoice_id="inv-three",
                invoice_number="INV-3",
                order_id=None,
                customer_id=lead.lead_id,
                status="OPEN",
                issue_date=date.today(),
                due_date=None,
                currency="usd",
                subtotal_cents=30000,
                tax_cents=0,
                total_cents=30000,
                notes=None,
                created_by=None,
            )
            session.add_all([invoice_one, invoice_two, invoice_three])
            await session.flush()

            payment = Payment(
                invoice_id=invoice_one.invoice_id,
                booking_id=booking_one.booking_id,
                provider="manual",
                provider_ref=None,
                checkout_session_id=None,
                payment_intent_id=None,
                method="cash",
                amount_cents=5000,
                currency="usd",
                status=invoice_statuses.PAYMENT_STATUS_SUCCEEDED,
                received_at=datetime.now(timezone.utc),
                reference=None,
            )
            session.add(payment)
            await session.commit()
            return client_one.client_id, client_two.client_id

    client_id, _ = asyncio.run(seed_data())
    token = issue_magic_token(
        "client1@example.com",
        client_id,
        secret=settings.client_portal_secret,
        ttl_minutes=30,
    )
    client.cookies.set("client_session", token)

    response = client.get("/client/invoices")
    assert response.status_code == 200
    invoices = response.json()
    assert {inv["invoice_id"] for inv in invoices} == {"inv-one", "inv-three"}
    balance = {inv["invoice_id"]: inv["balance_due_cents"] for inv in invoices}
    assert balance["inv-one"] == 5000
    assert balance["inv-three"] == 30000


def test_admin_config_and_flags_redact_secrets(client):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"
    settings.auth_secret_key = "top-secret-key"
    settings.deposits_enabled = True
    settings.export_mode = "webhook"
    headers = _auth_headers(settings.admin_basic_username, settings.admin_basic_password)

    config_response = client.get("/v1/admin/config", headers=headers)
    assert config_response.status_code == 200
    entries = {entry["key"]: entry for entry in config_response.json()["entries"]}
    assert entries["auth_secret_key"]["redacted"] is True
    assert entries["auth_secret_key"]["value"] != settings.auth_secret_key

    flags_response = client.get("/v1/admin/feature-flags", headers=headers)
    assert flags_response.status_code == 200
    flags = {flag["key"]: flag for flag in flags_response.json()["flags"]}
    assert flags["deposits"]["enabled"] is True
    assert flags["exports"]["enabled"] is True


def test_admin_can_pause_subscription_with_reason(client, async_session_maker):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"
    headers = _auth_headers(settings.admin_basic_username, settings.admin_basic_password)

    async def seed_subscription() -> str:
        async with async_session_maker() as session:
            client_user = ClientUser(email="sub@example.com")
            session.add(client_user)
            await session.flush()

            payload = subscription_schemas.SubscriptionCreateRequest(
                frequency=subscription_statuses.WEEKLY,
                start_date=date.today(),
                preferred_weekday=0,
                preferred_day_of_month=None,
                base_service_type="standard",
                base_price=1000,
            )
            subscription = await subscription_service.create_subscription(
                session, client_user.client_id, payload
            )
            await session.commit()
            return subscription.subscription_id

    subscription_id = asyncio.run(seed_subscription())
    new_next_run = datetime.now(timezone.utc) + timedelta(days=14)
    response = client.patch(
        f"/v1/admin/subscriptions/{subscription_id}",
        json={
            "status": subscription_statuses.PAUSED,
            "status_reason": "client requested pause",
            "next_run_at": new_next_run.isoformat(),
        },
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == subscription_statuses.PAUSED
    assert body["status_reason"] == "client requested pause"

    async def verify_status():
        async with async_session_maker() as session:
            result = await session.execute(
                sa.select(Subscription).where(Subscription.subscription_id == subscription_id)
            )
            record = result.scalar_one()
            assert record.status == subscription_statuses.PAUSED
            assert record.status_reason == "client requested pause"
            normalized_next_run = record.next_run_at
            if normalized_next_run.tzinfo is None:
                normalized_next_run = normalized_next_run.replace(tzinfo=timezone.utc)
            assert abs((normalized_next_run - new_next_run).total_seconds()) < 5

    asyncio.run(verify_status())


def test_ticket_filters_require_org_scope(client, async_session_maker):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"
    headers = _auth_headers(settings.admin_basic_username, settings.admin_basic_password)

    async def seed_tickets() -> None:
        async with async_session_maker() as session:
            booking_one = Booking(
                booking_id="ticket-order-1",
                client_id=None,
                team_id=1,
                starts_at=datetime.now(timezone.utc),
                duration_minutes=60,
                planned_minutes=60,
                status="PENDING",
                deposit_required=False,
                deposit_policy=[],
                consent_photos=False,
            )
            booking_two = Booking(
                booking_id="ticket-order-2",
                client_id=None,
                team_id=1,
                starts_at=datetime.now(timezone.utc),
                duration_minutes=60,
                planned_minutes=60,
                status="PENDING",
                deposit_required=False,
                deposit_policy=[],
                consent_photos=False,
            )
            session.add_all([booking_one, booking_two])
            await session.flush()

            ticket_one = SupportTicket(
                order_id=booking_one.booking_id,
                client_id=None,
                status="OPEN",
                priority="normal",
                subject="Low NPS",
                body="Follow up",
            )
            ticket_two = SupportTicket(
                order_id=booking_two.booking_id,
                client_id=None,
                status="RESOLVED",
                priority="normal",
                subject="Resolved",
                body="Handled",
            )
            session.add_all([ticket_one, ticket_two])
            await session.commit()

    asyncio.run(seed_tickets())

    filtered = client.get("/api/admin/tickets", params={"status": "RESOLVED"}, headers=headers)
    assert filtered.status_code == 200
    results = filtered.json()["tickets"]
    assert len(results) == 1
    assert results[0]["status"] == "RESOLVED"
