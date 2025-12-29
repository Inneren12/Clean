from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.domain.bookings.db_models import Booking
from app.domain.disputes import DecisionType, DisputeFacts, DisputeState
from app.domain.disputes.db_models import FinancialAdjustmentEvent
from app.domain.disputes import service as dispute_service
from app.domain.errors import DomainError
from app.domain.invoices.db_models import Invoice, InvoiceItem
from app.domain.leads.db_models import Lead


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


@pytest.mark.anyio
async def test_refund_with_invoice_fallback(async_session_maker):
    """Test that refunds work for existing bookings with base_charge_cents=0 but invoice exists"""
    async with async_session_maker() as session:
        # Create booking with base_charge_cents=0 (simulating existing booking)
        booking = Booking(
            team_id=1,  # Use default team from fixtures
            starts_at=datetime.now(tz=timezone.utc),
            duration_minutes=120,
            status="DONE",
            base_charge_cents=0,  # Not populated (existing booking)
        )
        session.add(booking)
        await session.flush()

        # Create invoice with subtotal (this is what we'll use for fallback)
        invoice = Invoice(
            invoice_number="INV-2025-TEST-001",
            order_id=booking.booking_id,
            status="paid",
            issue_date=datetime.now(tz=timezone.utc).date(),
            currency="CAD",
            subtotal_cents=25000,
            tax_cents=3250,
            total_cents=28250,
        )
        session.add(invoice)
        await session.flush()

        # Open dispute and request partial refund
        dispute = await dispute_service.open_dispute(
            session, booking.booking_id, reason="Service issue", opened_by="client"
        )
        await dispute_service.attach_facts(
            session,
            dispute.dispute_id,
            DisputeFacts(photo_refs=["photo-1"], checklist_snapshot={"score": 75}),
        )

        # Decide with partial refund - should use invoice subtotal as fallback
        await dispute_service.decide_dispute(
            session,
            dispute.dispute_id,
            decision=DecisionType.PARTIAL_REFUND,
            amount_cents=10000,
            notes="Partial refund using invoice fallback",
        )
        await session.refresh(booking)
        await session.refresh(dispute)

        assert dispute.decision_cents == 10000
        assert booking.refund_total_cents == 10000
        assert dispute.state == DisputeState.DECIDED.value


@pytest.mark.anyio
async def test_refund_with_lead_estimate_fallback(async_session_maker):
    """Test that refunds work for bookings with base_charge_cents=0 using lead estimate"""
    async with async_session_maker() as session:
        # Create lead with estimate snapshot
        lead = Lead(
            name="Test Client",
            phone="780-555-9876",
            email="test@example.com",
            postal_code="M5V3A8",
            estimate_snapshot={"total_before_tax": 180.00, "bedrooms": 2},
            structured_inputs={"beds": 2, "baths": 1},
            pricing_config_version="v1",
            config_hash="test-hash",
            referral_code="TESTREF01",
        )
        session.add(lead)
        await session.flush()

        # Create booking with base_charge_cents=0 but linked to lead
        booking = Booking(
            team_id=1,  # Use default team from fixtures
            lead_id=lead.lead_id,
            starts_at=datetime.now(tz=timezone.utc),
            duration_minutes=90,
            status="DONE",
            base_charge_cents=0,  # Not populated
        )
        session.add(booking)
        await session.flush()

        # Open dispute and request refund
        dispute = await dispute_service.open_dispute(
            session, booking.booking_id, reason="Quality issue"
        )
        await dispute_service.attach_facts(
            session,
            dispute.dispute_id,
            DisputeFacts(
                photo_refs=["photo-1", "photo-2"],
                checklist_snapshot={"score": 60, "issues": ["bathroom", "kitchen"]},
            ),
        )

        # Decide with full refund - should use lead estimate as fallback (180.00 * 100 = 18000 cents)
        await dispute_service.decide_dispute(
            session,
            dispute.dispute_id,
            decision=DecisionType.FULL_REFUND,
            notes="Full refund using lead estimate fallback",
        )
        await session.refresh(booking)
        await session.refresh(dispute)

        # Lead estimate is 180.00, which becomes 18000 cents
        assert dispute.decision_cents == 18000
        assert booking.refund_total_cents == 18000


@pytest.mark.anyio
async def test_refund_fails_without_base_charge_or_fallback(async_session_maker):
    """Test that refund fails when base_charge_cents=0 and no fallback sources exist"""
    async with async_session_maker() as session:
        # Create booking without base_charge_cents, invoice, or lead estimate
        booking = Booking(
            team_id=1,  # Use default team from fixtures
            starts_at=datetime.now(tz=timezone.utc),
            duration_minutes=60,
            status="DONE",
            base_charge_cents=0,
        )
        session.add(booking)
        await session.flush()

        dispute = await dispute_service.open_dispute(session, booking.booking_id)
        await dispute_service.attach_facts(
            session,
            dispute.dispute_id,
            DisputeFacts(photo_refs=["photo-1"]),
        )

        # Should raise error when trying to decide refund without any base charge source
        with pytest.raises(DomainError) as exc_info:
            await dispute_service.decide_dispute(
                session,
                dispute.dispute_id,
                decision=DecisionType.PARTIAL_REFUND,
                amount_cents=5000,
            )

        # Verify the error detail contains the expected message
        assert "Cannot determine refundable amount" in exc_info.value.detail
        assert "base charge missing and no invoice/estimate found" in exc_info.value.detail
