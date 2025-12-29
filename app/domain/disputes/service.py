from __future__ import annotations

import copy
from datetime import datetime, timezone
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.bookings.db_models import Booking
from app.domain.disputes.db_models import Dispute, FinancialAdjustmentEvent
from app.domain.disputes.schemas import DecisionType, DisputeFacts, DisputeState
from app.domain.errors import DomainError
from app.domain.invoices.db_models import Invoice

logger = logging.getLogger(__name__)


def _deepcopy(data: object) -> object:
    return copy.deepcopy(data)


async def _get_dispute(session: AsyncSession, dispute_id: str) -> Dispute:
    result = await session.execute(
        select(Dispute).where(Dispute.dispute_id == dispute_id).limit(1)
    )
    dispute = result.scalar_one_or_none()
    if not dispute:
        raise DomainError(detail="Dispute not found")
    return dispute


def _ensure_state(dispute: Dispute, allowed: set[DisputeState]) -> None:
    if DisputeState(dispute.state) not in allowed:
        raise DomainError(detail=f"Invalid dispute state transition from {dispute.state}")


async def _derive_effective_base_charge(session: AsyncSession, booking: Booking) -> int:
    """
    Derive effective base charge for bookings where base_charge_cents was not populated.
    Tries invoice subtotal first, then lead estimate snapshot.
    """
    if booking.base_charge_cents > 0:
        return booking.base_charge_cents

    # Try to get base charge from invoice
    result = await session.execute(
        select(Invoice.subtotal_cents)
        .where(Invoice.order_id == booking.booking_id)
        .order_by(Invoice.created_at.asc())
        .limit(1)
    )
    invoice_subtotal = result.scalar_one_or_none()
    if invoice_subtotal is not None and invoice_subtotal > 0:
        return invoice_subtotal

    # Try to get from lead estimate snapshot
    if booking.lead:
        estimate_snapshot = booking.lead.estimate_snapshot or {}
        total_before_tax = estimate_snapshot.get("total_before_tax")
        if total_before_tax is not None:
            try:
                import math
                return math.ceil(float(total_before_tax) * 100)
            except (ValueError, TypeError):
                pass

    # Last resort: check policy snapshot
    if booking.policy_snapshot:
        total_amount_cents = booking.policy_snapshot.get("total_amount_cents")
        if total_amount_cents is not None and total_amount_cents > 0:
            return total_amount_cents

    raise DomainError(
        detail="Cannot determine refundable amount; base charge missing and no invoice/estimate found"
    )


async def _validate_refund_amount(
    session: AsyncSession, booking: Booking, amount_cents: int
) -> int:
    effective_base = await _derive_effective_base_charge(session, booking)
    available = max(effective_base - booking.refund_total_cents, 0)
    if amount_cents <= 0:
        raise DomainError(detail="Refund amount must be positive")
    if amount_cents > available:
        raise DomainError(detail="Refund exceeds remaining charge amount")
    return amount_cents


async def _decision_amount(
    session: AsyncSession, booking: Booking, decision: DecisionType, amount_cents: int | None
) -> int:
    if decision == DecisionType.NO_REFUND:
        return 0
    if decision == DecisionType.CREDIT_NOTE:
        if amount_cents is None or amount_cents <= 0:
            raise DomainError(detail="Credit note requires a positive amount")
        return amount_cents
    if decision == DecisionType.PARTIAL_REFUND:
        if amount_cents is None:
            raise DomainError(detail="Partial refund requires amount_cents")
        return await _validate_refund_amount(session, booking, amount_cents)
    if decision == DecisionType.FULL_REFUND:
        effective_base = await _derive_effective_base_charge(session, booking)
        available = max(effective_base - booking.refund_total_cents, 0)
        if amount_cents is None:
            return available
        return await _validate_refund_amount(session, booking, amount_cents)
    raise DomainError(detail="Unsupported decision type")


async def open_dispute(
    session: AsyncSession,
    booking_id: str,
    *,
    reason: str | None = None,
    opened_by: str | None = None,
) -> Dispute:
    booking = await session.get(Booking, booking_id)
    if not booking:
        raise DomainError(detail="Booking not found")

    dispute = Dispute(
        booking_id=booking_id,
        state=DisputeState.OPEN.value,
        reason=reason,
        opened_by=opened_by,
    )
    session.add(dispute)
    await session.flush()
    return dispute


async def attach_facts(
    session: AsyncSession,
    dispute_id: str,
    facts: DisputeFacts,
) -> Dispute:
    dispute = await _get_dispute(session, dispute_id)
    if dispute.decision_snapshot:
        raise DomainError(detail="Dispute snapshot is immutable after decision")
    _ensure_state(dispute, {DisputeState.OPEN, DisputeState.FACTS_COLLECTED})

    dispute.facts_snapshot = _deepcopy(facts.model_dump())
    dispute.state = DisputeState.FACTS_COLLECTED.value
    await session.flush()
    return dispute


async def decide_dispute(
    session: AsyncSession,
    dispute_id: str,
    *,
    decision: DecisionType,
    amount_cents: int | None = None,
    notes: str | None = None,
) -> Dispute:
    dispute = await _get_dispute(session, dispute_id)
    _ensure_state(dispute, {DisputeState.FACTS_COLLECTED})

    # Load booking with lead relationship for fallback base charge derivation
    result = await session.execute(
        select(Booking).where(Booking.booking_id == dispute.booking_id).options(selectinload(Booking.lead))
    )
    booking = result.scalar_one_or_none()
    if not booking:
        raise DomainError(detail="Booking not found for dispute")
    if not dispute.facts_snapshot:
        raise DomainError(detail="Cannot decide dispute without evidence snapshot")

    decision_amount = await _decision_amount(session, booking, decision, amount_cents)
    now = datetime.now(timezone.utc)

    before_totals = {
        "base_charge_cents": booking.base_charge_cents,
        "refund_total_cents": booking.refund_total_cents,
        "credit_note_total_cents": booking.credit_note_total_cents,
    }

    if decision in {DecisionType.PARTIAL_REFUND, DecisionType.FULL_REFUND}:
        booking.refund_total_cents += decision_amount
    elif decision == DecisionType.CREDIT_NOTE:
        booking.credit_note_total_cents += decision_amount

    after_totals = {
        "base_charge_cents": booking.base_charge_cents,
        "refund_total_cents": booking.refund_total_cents,
        "credit_note_total_cents": booking.credit_note_total_cents,
    }

    dispute.decision = decision.value
    dispute.decision_cents = decision_amount
    dispute.decision_notes = notes
    dispute.decided_at = now
    dispute.state = DisputeState.DECIDED.value
    dispute.decision_snapshot = _deepcopy(
        {
            "facts": dispute.facts_snapshot,
            "decision": decision.value,
            "decision_cents": decision_amount,
            "recorded_at": now.isoformat(),
            "before_totals": before_totals,
            "after_totals": after_totals,
        }
    )

    event = FinancialAdjustmentEvent(
        booking_id=booking.booking_id,
        dispute_id=dispute.dispute_id,
        adjustment_type=decision.value,
        amount_cents=decision_amount,
        before_totals=before_totals,
        after_totals=after_totals,
    )
    session.add(event)

    await session.flush()
    logger.info(
        "dispute_decided",
        extra={
            "extra": {
                "event": "dispute_decided",
                "dispute_id": dispute.dispute_id,
                "booking_id": dispute.booking_id,
                "decision": decision.value,
                "amount_cents": decision_amount,
            }
        },
    )
    return dispute


async def close_dispute(
    session: AsyncSession,
    dispute_id: str,
    *,
    resolution_note: str | None = None,
) -> Dispute:
    dispute = await _get_dispute(session, dispute_id)
    _ensure_state(dispute, {DisputeState.DECIDED})
    dispute.state = DisputeState.CLOSED.value
    dispute.closed_at = datetime.now(timezone.utc)
    dispute.resolution_note = resolution_note
    await session.flush()
    return dispute
