from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.bookings.db_models import Booking
from app.domain.invoices.db_models import InvoiceItem
from app.domain.leads.db_models import Lead
from app.domain.reason_logs.db_models import ReasonLog
from app.domain.reason_logs.schemas import (
    PRICE_ADJUST_CODES,
    TIME_OVERRUN_CODES,
    ReasonCode,
    ReasonKind,
)
from app.domain.time_tracking.db_models import WorkTimeEntry

logger = logging.getLogger(__name__)


def _allowed_codes(kind: ReasonKind) -> set[ReasonCode]:
    if kind == ReasonKind.TIME_OVERRUN:
        return TIME_OVERRUN_CODES
    if kind == ReasonKind.PRICE_ADJUST:
        return PRICE_ADJUST_CODES
    return set()


def estimate_subtotal_from_lead(lead: Lead | None) -> int | None:
    if lead is None:
        return None
    snapshot = lead.estimate_snapshot or {}
    for key in ("subtotal_cents", "price_cents", "total_before_tax"):
        value = snapshot.get(key)
        if value is None:
            continue
        try:
            cents = int(round(float(value)))
            return cents
        except (TypeError, ValueError):
            continue
    return None


async def _load_order(session: AsyncSession, order_id: str) -> Booking | None:
    return await session.get(Booking, order_id)


async def _ensure_time_entry(
    session: AsyncSession, time_entry_id: str, order_id: str
) -> WorkTimeEntry:
    entry = await session.get(WorkTimeEntry, time_entry_id)
    if entry is None or entry.booking_id != order_id:
        raise ValueError("Invalid time entry reference")
    return entry


async def _ensure_invoice_item(
    session: AsyncSession, invoice_item_id: int, order_id: str
) -> InvoiceItem:
    item = await session.get(InvoiceItem, invoice_item_id)
    if item is None or getattr(item, "invoice", None) is None:
        raise ValueError("Invalid invoice item reference")
    invoice = item.invoice
    if invoice and invoice.order_id != order_id:
        raise ValueError("Invoice item does not belong to order")
    return item


async def create_reason(
    session: AsyncSession,
    order_id: str,
    *,
    kind: ReasonKind,
    code: ReasonCode,
    note: str | None = None,
    created_by: str | None = None,
    time_entry_id: str | None = None,
    invoice_item_id: int | None = None,
) -> ReasonLog:
    order = await _load_order(session, order_id)
    if order is None:
        raise ValueError("Order not found")

    if code not in _allowed_codes(kind):
        raise ValueError("Invalid code for reason kind")

    if time_entry_id:
        await _ensure_time_entry(session, time_entry_id, order_id)
    if invoice_item_id:
        await _ensure_invoice_item(session, invoice_item_id, order_id)

    reason = ReasonLog(
        order_id=order_id,
        kind=kind.value,
        code=code.value,
        note=note,
        created_by=created_by,
        time_entry_id=time_entry_id,
        invoice_item_id=invoice_item_id,
    )
    session.add(reason)
    await session.flush()
    logger.info(
        "reason_log_created",
        extra={"extra": {"order_id": order_id, "kind": kind.value, "code": code.value}},
    )
    return reason


async def list_reasons_for_order(session: AsyncSession, order_id: str) -> list[ReasonLog]:
    stmt = (
        select(ReasonLog)
        .where(ReasonLog.order_id == order_id)
        .order_by(ReasonLog.created_at.desc())
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def has_reason(
    session: AsyncSession,
    order_id: str,
    *,
    kind: ReasonKind,
    time_entry_id: str | None = None,
) -> bool:
    conditions: list[object] = [ReasonLog.order_id == order_id, ReasonLog.kind == kind.value]
    if time_entry_id:
        conditions.append(or_(ReasonLog.time_entry_id == time_entry_id, ReasonLog.time_entry_id.is_(None)))
    stmt = select(ReasonLog.reason_id).where(and_(*conditions)).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def fetch_reasons(
    session: AsyncSession,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    kind: ReasonKind | None = None,
) -> list[ReasonLog]:
    stmt: Select = select(ReasonLog)
    filters = []
    if start:
        filters.append(ReasonLog.created_at >= start)
    if end:
        filters.append(ReasonLog.created_at <= end)
    if kind:
        filters.append(ReasonLog.kind == kind.value)
    if filters:
        stmt = stmt.where(and_(*filters))
    stmt = stmt.order_by(ReasonLog.created_at.desc())
    result = await session.execute(stmt)
    return result.scalars().all()
