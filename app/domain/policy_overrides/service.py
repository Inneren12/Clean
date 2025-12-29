from __future__ import annotations

import copy
import logging
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.bookings.db_models import Booking
from app.domain.policy_overrides.db_models import PolicyOverrideAudit
from app.domain.policy_overrides.schemas import OverrideType

logger = logging.getLogger(__name__)


async def record_override(
    session: AsyncSession,
    *,
    booking_id: str,
    override_type: OverrideType,
    actor: str,
    reason: str,
    old_value: dict[str, Any],
    new_value: dict[str, Any],
    flush: bool = True,
) -> PolicyOverrideAudit:
    audit = PolicyOverrideAudit(
        booking_id=booking_id,
        override_type=override_type.value,
        actor=actor,
        reason=reason,
        old_value=copy.deepcopy(old_value),
        new_value=copy.deepcopy(new_value),
    )
    session.add(audit)
    if flush:
        await session.flush()
    return audit


async def apply_override(
    session: AsyncSession,
    *,
    booking_id: str,
    override_type: OverrideType,
    actor: str,
    reason: str,
    payload: dict[str, Any],
    commit: bool = False,
) -> tuple[Booking, PolicyOverrideAudit]:
    if not reason or not reason.strip():
        raise ValueError("Override reason is required")

    def _apply_mutation(booking: Booking) -> tuple[dict[str, Any], dict[str, Any]]:
        if override_type == OverrideType.RISK_BAND:
            band_value = payload.get("risk_band")
            band_str = band_value.value if hasattr(band_value, "value") else str(band_value)
            risk_score = booking.risk_score
            if "risk_score" in payload and payload.get("risk_score") is not None:
                risk_score = int(payload.get("risk_score"))

            risk_reasons = list(booking.risk_reasons)
            if "risk_reasons" in payload and payload.get("risk_reasons") is not None:
                risk_reasons = list(payload.get("risk_reasons"))
            old_value = {
                "risk_band": booking.risk_band,
                "risk_score": booking.risk_score,
                "risk_reasons": list(booking.risk_reasons),
            }
            booking.risk_band = band_str
            booking.risk_score = risk_score
            booking.risk_reasons = risk_reasons
            new_value = {
                "risk_band": booking.risk_band,
                "risk_score": booking.risk_score,
                "risk_reasons": list(booking.risk_reasons),
            }
        elif override_type == OverrideType.DEPOSIT_REQUIRED:
            deposit_required = bool(payload.get("deposit_required"))
            deposit_policy = list(payload.get("deposit_policy") or [])
            deposit_status = payload.get("deposit_status")
            deposit_cents = payload.get("deposit_cents", booking.deposit_cents)
            old_value = {
                "deposit_required": booking.deposit_required,
                "deposit_cents": booking.deposit_cents,
                "deposit_policy": list(booking.deposit_policy),
                "deposit_status": booking.deposit_status,
            }
            booking.deposit_required = deposit_required
            booking.deposit_policy = deposit_policy
            booking.deposit_status = deposit_status
            booking.deposit_cents = int(deposit_cents) if deposit_cents is not None else None
            if not deposit_required:
                booking.deposit_cents = None
            new_value = {
                "deposit_required": booking.deposit_required,
                "deposit_cents": booking.deposit_cents,
                "deposit_policy": list(booking.deposit_policy),
                "deposit_status": booking.deposit_status,
            }
        elif override_type == OverrideType.DEPOSIT_AMOUNT:
            if "deposit_cents" not in payload:
                raise ValueError("deposit_cents is required for DEPOSIT_AMOUNT overrides")
            deposit_cents = payload["deposit_cents"]
            old_value = {
                "deposit_required": booking.deposit_required,
                "deposit_cents": booking.deposit_cents,
            }
            booking.deposit_cents = int(deposit_cents) if deposit_cents is not None else None
            new_value = {
                "deposit_required": booking.deposit_required,
                "deposit_cents": booking.deposit_cents,
            }
        elif override_type == OverrideType.CANCELLATION_POLICY:
            policy_snapshot = payload.get("policy_snapshot")
            if not policy_snapshot:
                raise ValueError("policy_snapshot is required for cancellation policy override")
            old_value = {"policy_snapshot": copy.deepcopy(booking.policy_snapshot)}
            booking.policy_snapshot = copy.deepcopy(policy_snapshot)
            new_value = {"policy_snapshot": copy.deepcopy(booking.policy_snapshot)}
        elif override_type == OverrideType.CANCELLATION_EXCEPTION:
            granted = bool(payload.get("cancellation_exception", True))
            note = payload.get("note")
            old_value = {
                "cancellation_exception": booking.cancellation_exception,
                "note": booking.cancellation_exception_note,
            }
            booking.cancellation_exception = granted
            if note is not None:
                booking.cancellation_exception_note = note
            new_value = {
                "cancellation_exception": booking.cancellation_exception,
                "note": booking.cancellation_exception_note,
            }
        else:
            raise ValueError("Unsupported override type")
        return old_value, new_value

    started_transaction = False
    if not session.in_transaction():
        await session.begin()
        started_transaction = True

    try:
        with session.no_autoflush:
            stmt = (
                select(Booking)
                .where(Booking.booking_id == booking_id)
                .limit(1)
                .with_for_update()
            )
            booking = (await session.execute(stmt)).scalar_one_or_none()
            if booking is None:
                raise ValueError("Booking not found")
            if booking.status == "DONE" and override_type != OverrideType.CANCELLATION_EXCEPTION:
                raise ValueError("Overrides not allowed after booking completion")

            old_value, new_value = _apply_mutation(booking)

            audit = await record_override(
                session,
                booking_id=booking.booking_id,
                override_type=override_type,
                actor=actor,
                reason=reason,
                old_value=old_value,
                new_value=new_value,
                flush=commit,
            )

            if commit:
                await session.flush()
                await session.commit()
                await session.refresh(booking)
    except Exception:
        if session.in_transaction():
            await session.rollback()
        raise
    finally:
        if started_transaction and commit is False and session.in_transaction():
            # Keep the transaction open for the caller to finish (commit/rollback)
            pass

    logger.info(
        "policy_override_applied",
        extra={
            "extra": {
                "event": "policy_override_applied",
                "booking_id": booking_id,
                "override_type": override_type.value,
                "actor": actor,
            }
        },
    )
    return booking, audit


async def list_overrides(
    session: AsyncSession,
    *,
    booking_id: str | None = None,
    override_type: OverrideType | None = None,
) -> list[PolicyOverrideAudit]:
    stmt: Select[tuple[PolicyOverrideAudit]] = select(PolicyOverrideAudit)
    filters: list[object] = []
    if booking_id:
        filters.append(PolicyOverrideAudit.booking_id == booking_id)
    if override_type:
        filters.append(PolicyOverrideAudit.override_type == override_type.value)
    if filters:
        stmt = stmt.where(*filters)
    stmt = stmt.order_by(PolicyOverrideAudit.created_at.desc())
    result = await session.execute(stmt)
    return result.scalars().all()
