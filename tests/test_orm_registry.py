import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import configure_mappers

from app.domain.bookings.db_models import Booking, EmailEvent
from app.infra.db import Base


@pytest.mark.anyio
async def test_booking_mapper_configures_without_worker_import():
    # Ensure string-based relationships resolve without manually importing related modules
    configure_mappers()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    assert any(rel.mapper.class_.__name__ == "Worker" for rel in Booking.__mapper__.relationships)

    await engine.dispose()


@pytest.mark.anyio
async def test_email_event_resolves_invoice_relationship():
    configure_mappers()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    assert any(rel.mapper.class_.__name__ == "Invoice" for rel in EmailEvent.__mapper__.relationships)

    await engine.dispose()
