from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.domain.bookings.db_models import Booking
from app.domain.disputes import DecisionType, DisputeFacts, DisputeState
from app.domain.disputes.db_models import FinancialAdjustmentEvent
from app.domain.disputes import service as dispute_service
from app.domain.errors import DomainError


@pytest.mark.anyio
async def test_dispute_lifecycle_with_partial_refund(async_session_maker):
    async with async_session_maker() as session:
        booking = Booking(
            team_id=1,
            starts_at=datetime.now(tz=timezone.utc),
            duration_minutes=90,
            status="DONE",
            base_charge_cents=20000,
        )
        session.add(booking)
        await session.flush()

        dispute = await dispute_service.open_dispute(
            session,
            booking.booking_id,
            reason="Quality concern",
            opened_by="client",
        )

        facts = DisputeFacts(
            photo_refs=["photo-1", "photo-2"],
            checklist_snapshot={"score": 80, "notes": "missed spots"},
            time_log={"total_seconds": 3600},
        )
        await dispute_service.attach_facts(session, dispute.dispute_id, facts)
        await dispute_service.decide_dispute(
            session,
            dispute.dispute_id,
            decision=DecisionType.PARTIAL_REFUND,
            amount_cents=5000,
            notes="Partial refund for rework",
        )
        await session.flush()
        await session.refresh(dispute)
        await session.refresh(booking)

        assert dispute.state == DisputeState.DECIDED.value
        assert dispute.decision_cents == 5000
        assert booking.refund_total_cents == 5000
        assert dispute.decision_snapshot["facts"]["photo_refs"] == ["photo-1", "photo-2"]

        events = (
            await session.execute(
                select(FinancialAdjustmentEvent).where(
                    FinancialAdjustmentEvent.dispute_id == dispute.dispute_id
                )
            )
        ).scalars().all()
        assert len(events) == 1
        assert events[0].amount_cents == 5000

        await dispute_service.close_dispute(session, dispute.dispute_id, resolution_note="Done")
        await session.refresh(dispute)
        assert dispute.state == DisputeState.CLOSED.value


@pytest.mark.anyio
async def test_full_refund_and_snapshot_immutability(async_session_maker):
    async with async_session_maker() as session:
        booking = Booking(
            team_id=1,
            starts_at=datetime.now(tz=timezone.utc),
            duration_minutes=60,
            status="DONE",
            base_charge_cents=15000,
        )
        booking.refund_total_cents = 2000
        session.add(booking)
        await session.flush()

        dispute = await dispute_service.open_dispute(session, booking.booking_id, opened_by="ops")
        await dispute_service.attach_facts(
            session,
            dispute.dispute_id,
            DisputeFacts(photo_refs=["p-before"], checklist_snapshot={"score": 70}),
        )

        await dispute_service.decide_dispute(
            session,
            dispute.dispute_id,
            decision=DecisionType.FULL_REFUND,
            notes="Full refund after review",
        )
        await session.refresh(booking)
        await session.refresh(dispute)

        assert booking.refund_total_cents == 15000
        assert dispute.decision_snapshot["after_totals"]["refund_total_cents"] == 15000

        with pytest.raises(DomainError):
            await dispute_service.attach_facts(
                session,
                dispute.dispute_id,
                DisputeFacts(photo_refs=["should-not-apply"]),
            )

        events = (
            await session.execute(
                select(FinancialAdjustmentEvent).where(
                    FinancialAdjustmentEvent.dispute_id == dispute.dispute_id
                )
            )
        ).scalars().all()
        assert events[0].adjustment_type == DecisionType.FULL_REFUND.value
        assert events[0].before_totals["refund_total_cents"] == 2000
        assert events[0].after_totals["refund_total_cents"] == 15000
