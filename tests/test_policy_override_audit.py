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
async def test_risk_override_preserves_score_and_reasons_when_omitted(async_session_maker):
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
            risk_score=250,
            risk_reasons=["automated_review", "history"],
        )
        session.add(booking)
        await session.commit()
        await session.refresh(booking)

        await booking_service.override_risk_band(
            session,
            booking.booking_id,
            actor="admin",
            reason="manual review",
            new_band=booking_service.RiskBand.MEDIUM,
        )

        audits = await override_service.list_overrides(
            session, booking_id=booking.booking_id, override_type=OverrideType.RISK_BAND
        )

        assert len(audits) == 1
        audit = audits[0]
        assert audit.old_value["risk_band"] == "LOW"
        assert audit.old_value["risk_score"] == 250
        assert audit.old_value["risk_reasons"] == ["automated_review", "history"]
        assert audit.new_value["risk_band"] == "MEDIUM"
        assert audit.new_value["risk_score"] == 250
        assert audit.new_value["risk_reasons"] == ["automated_review", "history"]


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


@pytest.mark.anyio
async def test_apply_override_does_not_rollback_outer_transaction(async_session_maker):
    """Test that apply_override does not rollback an outer transaction on error."""
    async with async_session_maker() as session:
        # Create a booking within an outer transaction
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
        await session.flush()
        booking_id = booking.booking_id

        # Try to apply an invalid override (empty reason)
        # This should fail, but should not rollback the outer transaction
        try:
            await override_service.apply_override(
                session,
                booking_id=booking_id,
                override_type=OverrideType.RISK_BAND,
                actor="admin",
                reason="",  # Invalid: empty reason
                payload={"risk_band": "HIGH"},
                commit=False,
            )
        except ValueError:
            pass  # Expected error

        # The outer transaction should still be active
        # and we should be able to commit the booking
        await session.commit()

    # Verify the booking was created despite the failed override
    async with async_session_maker() as read_session:
        created_booking = await read_session.get(Booking, booking_id)
        assert created_booking is not None
        assert created_booking.status == "PENDING"


@pytest.mark.anyio
async def test_audit_row_flushed_when_commit_false(async_session_maker):
    """Test that audit rows are flushed even when commit=False."""
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

        # Apply override with commit=False
        await booking_service.override_risk_band(
            session,
            booking.booking_id,
            actor="admin",
            reason="test",
            new_band=booking_service.RiskBand.HIGH,
            commit=False,
        )

        # Audit should be visible after flush
        audits = await override_service.list_overrides(
            session, booking_id=booking.booking_id, override_type=OverrideType.RISK_BAND
        )
        assert len(audits) == 1

        # Commit the transaction
        await session.commit()

    # Verify audit persisted
    async with async_session_maker() as read_session:
        audits = await override_service.list_overrides(
            read_session, booking_id=booking.booking_id, override_type=OverrideType.RISK_BAND
        )
        assert len(audits) == 1
        assert audits[0].new_value["risk_band"] == "HIGH"
