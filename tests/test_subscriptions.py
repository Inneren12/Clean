import asyncio
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select, func

from app.domain.bookings.db_models import Booking
from app.domain.clients import service as client_service
from app.domain.invoices.db_models import Invoice
from app.domain.subscriptions import schemas as subscription_schemas
from app.domain.subscriptions import service as subscription_service
from app.settings import settings


def _issue_client_token(client_id: str, email: str) -> str:
    return client_service.issue_magic_token(
        email=email,
        client_id=client_id,
        secret=settings.client_portal_secret,
        ttl_minutes=settings.client_portal_token_ttl_minutes,
    )


async def _create_client(async_session_maker, email: str = "user@example.com"):
    async with async_session_maker() as session:
        client = await client_service.get_or_create_client(session, email, commit=False)
        await session.commit()
        return client


def test_idempotent_generation(client, async_session_maker):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"

    client_user = asyncio.run(_create_client(async_session_maker))
    token = _issue_client_token(client_user.client_id, client_user.email)

    payload = {
        "frequency": "WEEKLY",
        "start_date": (date.today() - timedelta(days=1)).isoformat(),
        "base_service_type": "standard",
        "base_price": 12000,
    }
    response = client.post("/client/subscriptions", json=payload, cookies={"client_session": token})
    assert response.status_code == 201, response.text

    for _ in range(2):
        run_response = client.post("/v1/admin/subscriptions/run", auth=("admin", "secret"))
        assert run_response.status_code == 200

    async def _counts():
        async with async_session_maker() as session:
            bookings_count = await session.scalar(select(func.count(Booking.booking_id)))
            invoices_count = await session.scalar(select(func.count(Invoice.invoice_id)))
            return bookings_count, invoices_count

    bookings_count, invoices_count = asyncio.run(_counts())
    assert bookings_count == 1
    assert invoices_count == 1


def test_biweekly_schedule(async_session_maker):
    async def _prepare():
        async with async_session_maker() as session:
            client_user = await client_service.get_or_create_client(session, "biweekly@example.com", commit=False)
            payload = subscription_schemas.SubscriptionCreateRequest(
                frequency="BIWEEKLY",
                start_date=date(2024, 1, 1),
                base_service_type="standard",
                base_price=10000,
            )
            sub = await subscription_service.create_subscription(session, client_user.client_id, payload)
            await session.commit()
            await session.refresh(sub)
            return sub

    subscription = asyncio.run(_prepare())

    async def _generate(now: datetime):
        async with async_session_maker() as session:
            await subscription_service.generate_due_orders(session, now=now)
            await session.commit()
            result = await session.execute(
                select(Booking).where(Booking.subscription_id == subscription.subscription_id).order_by(Booking.scheduled_date)
            )
            return [row.scheduled_date for row in result.scalars().all()]

    first = asyncio.run(_generate(datetime(2024, 1, 2, 12, tzinfo=timezone.utc)))
    second = asyncio.run(_generate(datetime(2024, 1, 20, 12, tzinfo=timezone.utc)))
    third = asyncio.run(_generate(datetime(2024, 2, 3, 12, tzinfo=timezone.utc)))

    assert first == [date(2024, 1, 1)]
    assert second == [date(2024, 1, 1), date(2024, 1, 15)]
    assert third == [date(2024, 1, 1), date(2024, 1, 15), date(2024, 1, 29)]


def test_paused_subscription_blocks_generation(client, async_session_maker):
    settings.admin_basic_username = "admin"
    settings.admin_basic_password = "secret"

    client_user = asyncio.run(_create_client(async_session_maker, email="paused@example.com"))
    token = _issue_client_token(client_user.client_id, client_user.email)
    payload = {
        "frequency": "MONTHLY",
        "start_date": (date.today() - timedelta(days=2)).isoformat(),
        "base_service_type": "standard",
        "base_price": 15000,
        "preferred_day_of_month": 1,
    }
    response = client.post("/client/subscriptions", json=payload, cookies={"client_session": token})
    assert response.status_code == 201
    subscription_id = response.json()["subscription_id"]

    patch_resp = client.patch(
        f"/client/subscriptions/{subscription_id}",
        json={"status": "PAUSED"},
        cookies={"client_session": token},
    )
    assert patch_resp.status_code == 200

    run_response = client.post("/v1/admin/subscriptions/run", auth=("admin", "secret"))
    assert run_response.status_code == 200

    async def _bookings():
        async with async_session_maker() as session:
            result = await session.execute(select(func.count(Booking.booking_id)))
            return result.scalar_one()

    assert asyncio.run(_bookings()) == 0


def test_monthly_subscription_day_31_to_february(async_session_maker):
    """Regression test: Jan 31 should schedule to Feb 28 (or 29 in leap year), not crash."""
    async def _test():
        async with async_session_maker() as session:
            client_user = await client_service.get_or_create_client(session, "jan31@example.com", commit=False)

            # Create subscription on Jan 31 with no preferred_day_of_month
            payload = subscription_schemas.SubscriptionCreateRequest(
                frequency="MONTHLY",
                start_date=date(2024, 1, 31),
                base_service_type="standard",
                base_price=10000,
            )
            sub = await subscription_service.create_subscription(session, client_user.client_id, payload)
            await session.commit()
            await session.refresh(sub)

            # Verify preferred_day_of_month was set to 31
            assert sub.preferred_day_of_month == 31
            assert sub.next_run_at.date() == date(2024, 1, 31)

            # Generate order for Jan 31
            await subscription_service.generate_due_orders(session, now=datetime(2024, 2, 1, 23, tzinfo=timezone.utc))
            await session.commit()
            await session.refresh(sub)

            # Next run should be Feb 29, 2024 (leap year), clamped from 31
            assert sub.next_run_at.date() == date(2024, 2, 29)

            # Generate order for Feb 29
            await subscription_service.generate_due_orders(session, now=datetime(2024, 3, 1, 23, tzinfo=timezone.utc))
            await session.commit()
            await session.refresh(sub)

            # Next run should be Mar 31 (31 days available, back to preferred day)
            assert sub.next_run_at.date() == date(2024, 3, 31)

            # Verify bookings were created with correct dates
            result = await session.execute(
                select(Booking).where(Booking.subscription_id == sub.subscription_id).order_by(Booking.scheduled_date)
            )
            bookings = [row.scheduled_date for row in result.scalars().all()]
            # Should have Jan 31 and Feb 29, demonstrating no crash on short month
            assert bookings == [date(2024, 1, 31), date(2024, 2, 29)]

    asyncio.run(_test())


def test_monthly_subscription_day_31_to_april(async_session_maker):
    """Regression test: Mar 31 should schedule to Apr 30, not crash."""
    async def _test():
        async with async_session_maker() as session:
            client_user = await client_service.get_or_create_client(session, "mar31@example.com", commit=False)

            # Create subscription on Mar 31 with no preferred_day_of_month
            payload = subscription_schemas.SubscriptionCreateRequest(
                frequency="MONTHLY",
                start_date=date(2024, 3, 31),
                base_service_type="standard",
                base_price=10000,
            )
            sub = await subscription_service.create_subscription(session, client_user.client_id, payload)
            await session.commit()
            await session.refresh(sub)

            # Generate order for Mar 31
            await subscription_service.generate_due_orders(session, now=datetime(2024, 4, 1, 23, tzinfo=timezone.utc))
            await session.commit()
            await session.refresh(sub)

            # Next run should be Apr 30, clamped from 31
            assert sub.next_run_at.date() == date(2024, 4, 30)

            # Generate order for Apr 30
            await subscription_service.generate_due_orders(session, now=datetime(2024, 5, 1, 23, tzinfo=timezone.utc))
            await session.commit()
            await session.refresh(sub)

            # Next run should be May 31 (31 days available)
            assert sub.next_run_at.date() == date(2024, 5, 31)

    asyncio.run(_test())


def test_monthly_subscription_non_leap_year_february(async_session_maker):
    """Regression test: Jan 31 in non-leap year should schedule to Feb 28."""
    async def _test():
        async with async_session_maker() as session:
            client_user = await client_service.get_or_create_client(session, "jan31-2023@example.com", commit=False)

            # Create subscription on Jan 31, 2023 (non-leap year)
            payload = subscription_schemas.SubscriptionCreateRequest(
                frequency="MONTHLY",
                start_date=date(2023, 1, 31),
                base_service_type="standard",
                base_price=10000,
            )
            sub = await subscription_service.create_subscription(session, client_user.client_id, payload)
            await session.commit()
            await session.refresh(sub)

            # Generate order for Jan 31
            await subscription_service.generate_due_orders(session, now=datetime(2023, 2, 1, 23, tzinfo=timezone.utc))
            await session.commit()
            await session.refresh(sub)

            # Next run should be Feb 28, 2023 (non-leap year), clamped from 31
            assert sub.next_run_at.date() == date(2023, 2, 28)

    asyncio.run(_test())
