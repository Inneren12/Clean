import pytest
from datetime import datetime, timezone

from app.domain.bookings.db_models import Booking
from app.domain.bookings import service as booking_service
from app.domain.policy_overrides import service as override_service
from app.domain.policy_overrides.schemas import OverrideType


@pytest.mark.anyio
async def test_risk_override_is_audited(async_session_maker):
    async with async_session_maker() as session:
        booking = Booking(
            team_id=1,
            lead_id=None,
            starts_at=datetime.now(tz=timezone.utc),
            duration_minutes=60,
            planned_minutes=60,
            status="PENDING",
            deposit_required=False,
            deposit_policy=[],
            deposit_status=None,
        )
        session.add(booking)
        await session.commit()
        await session.refresh(booking)

        await booking_service.override_risk_band(
            session,
            booking.booking_id,
            actor="admin",
            reason="manual review",
            new_band=booking_service.RiskBand.HIGH,
            new_risk_score=999,
            new_risk_reasons=["manual_override"],
        )

        audits = await override_service.list_overrides(
            session, booking_id=booking.booking_id, override_type=OverrideType.RISK_BAND
        )
        assert len(audits) == 1
        audit = audits[0]
        assert audit.override_type == OverrideType.RISK_BAND.value
        assert audit.actor == "admin"
        assert audit.reason == "manual review"
        assert audit.old_value["risk_band"] == "LOW"
        assert audit.new_value["risk_band"] == "HIGH"
        assert audit.new_value["risk_score"] == 999
        assert audit.new_value["risk_reasons"] == ["manual_override"]


@pytest.mark.anyio
async def test_deposit_override_is_audited(async_session_maker):
    async with async_session_maker() as session:
        booking = Booking(
            team_id=1,
            lead_id=None,
            starts_at=datetime.now(tz=timezone.utc),
            duration_minutes=45,
            planned_minutes=45,
            status="PENDING",
            deposit_required=False,
            deposit_policy=[],
            deposit_status=None,
        )
        session.add(booking)
        await session.commit()
        await session.refresh(booking)

        await booking_service.override_deposit_policy(
            session,
            booking.booking_id,
            actor="ops",
            reason="payment risk",
            deposit_required=True,
            deposit_cents=5000,
            deposit_policy=["manual_override"],
            deposit_status="pending",
        )

        audits = await override_service.list_overrides(
            session,
            booking_id=booking.booking_id,
            override_type=OverrideType.DEPOSIT_REQUIRED,
        )
        assert len(audits) == 1
        audit = audits[0]
        assert audit.old_value["deposit_required"] is False
        assert audit.old_value["deposit_cents"] is None
        assert audit.new_value["deposit_required"] is True
        assert audit.new_value["deposit_cents"] == 5000
        assert audit.new_value["deposit_policy"] == ["manual_override"]
        assert audit.new_value["deposit_status"] == "pending"


@pytest.mark.anyio
async def test_cancellation_exception_audit_is_immutable(async_session_maker):
    async with async_session_maker() as session:
        booking = Booking(
            team_id=1,
            lead_id=None,
            starts_at=datetime.now(tz=timezone.utc),
            duration_minutes=30,
            planned_minutes=30,
            status="PENDING",
            deposit_required=False,
            deposit_policy=[],
            deposit_status=None,
        )
        session.add(booking)
        await session.commit()
        await session.refresh(booking)

        await booking_service.grant_cancellation_exception(
            session,
            booking.booking_id,
            actor="support",
            reason="blizzard",
            note="road closures",
        )

        audits = await override_service.list_overrides(
            session,
            booking_id=booking.booking_id,
            override_type=OverrideType.CANCELLATION_EXCEPTION,
        )
        assert len(audits) == 1
        audit = audits[0]
        assert audit.new_value["cancellation_exception"] is True
        assert audit.new_value["note"] == "road closures"

        with pytest.raises(ValueError):
            audit.reason = "tamper"
            await session.flush()
        await session.rollback()


@pytest.mark.anyio
async def test_apply_override_defers_commit(async_session_maker):
    booking_id: str

    async with async_session_maker() as session:
        booking = Booking(
            team_id=1,
            lead_id=None,
            starts_at=datetime.now(tz=timezone.utc),
            duration_minutes=30,
            planned_minutes=30,
            status="PENDING",
            deposit_required=False,
            deposit_policy=[],
            deposit_status=None,
        )
        session.add(booking)
        await session.commit()
        await session.refresh(booking)

        await booking_service.override_deposit_policy(
            session,
            booking.booking_id,
            actor="ops",
            reason="manual hold",
            deposit_required=True,
            deposit_cents=2500,
            deposit_policy=["manual_override"],
            deposit_status="pending",
            commit=False,
        )

        assert booking.deposit_required is True
        booking_id = booking.booking_id

        await session.rollback()

    async with async_session_maker() as read_session:
        reverted = await read_session.get(Booking, booking_id)
        assert reverted.deposit_required is False

    async with async_session_maker() as session:
        await booking_service.override_deposit_policy(
            session,
            booking_id,
            actor="ops",
            reason="manual hold",
            deposit_required=True,
            deposit_cents=2500,
            deposit_policy=["manual_override"],
            deposit_status="pending",
            commit=True,
        )

    async with async_session_maker() as read_session:
        persisted = await read_session.get(Booking, booking_id)
        assert persisted.deposit_required is True
