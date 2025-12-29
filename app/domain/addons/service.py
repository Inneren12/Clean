from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import Select, and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.addons import schemas
from app.domain.addons.db_models import AddonDefinition, OrderAddon
from app.domain.bookings.db_models import Booking
from app.domain.invoices.schemas import InvoiceItemCreate
from app.domain.leads.db_models import Lead

logger = logging.getLogger(__name__)


async def list_definitions(session: AsyncSession, include_inactive: bool = True) -> list[AddonDefinition]:
    stmt: Select[AddonDefinition] = select(AddonDefinition)
    if not include_inactive:
        stmt = stmt.where(AddonDefinition.is_active.is_(True))
    stmt = stmt.order_by(AddonDefinition.name)
    result = await session.execute(stmt)
    return result.scalars().all()


async def create_definition(
    session: AsyncSession, payload: schemas.AddonDefinitionCreate
) -> AddonDefinition:
    addon = AddonDefinition(
        code=payload.code,
        name=payload.name,
        price_cents=payload.price_cents,
        default_minutes=payload.default_minutes,
        is_active=payload.is_active,
    )
    session.add(addon)
    await session.flush()
    await session.refresh(addon)
    logger.info("addon_created", extra={"extra": {"addon_id": addon.addon_id, "code": addon.code}})
    return addon


async def update_definition(
    session: AsyncSession, addon_id: int, payload: schemas.AddonDefinitionUpdate
) -> AddonDefinition:
    addon = await session.get(AddonDefinition, addon_id)
    if addon is None:
        raise ValueError("Addon not found")

    if payload.code is not None:
        addon.code = payload.code
    if payload.name is not None:
        addon.name = payload.name
    if payload.price_cents is not None:
        addon.price_cents = payload.price_cents
    if payload.default_minutes is not None:
        addon.default_minutes = payload.default_minutes
    if payload.is_active is not None:
        addon.is_active = payload.is_active

    await session.flush()
    await session.refresh(addon)
    logger.info("addon_updated", extra={"extra": {"addon_id": addon.addon_id, "code": addon.code}})
    return addon


async def set_order_addons(
    session: AsyncSession, order_id: str, selections: list[schemas.OrderAddonSelection]
) -> list[OrderAddon]:
    order = await session.get(Booking, order_id)
    if order is None:
        raise ValueError("Order not found")

    addon_ids = [item.addon_id for item in selections]
    if len(addon_ids) != len(set(addon_ids)):
        raise ValueError("Duplicate addons are not allowed")

    existing_stmt = select(OrderAddon).where(OrderAddon.order_id == order_id)
    existing_result = await session.execute(existing_stmt)
    existing = existing_result.scalars().all()
    previous_minutes = sum(item.minutes_snapshot * item.qty for item in existing)

    if selections:
        def_stmt: Select[AddonDefinition] = select(AddonDefinition).where(
            and_(AddonDefinition.addon_id.in_(addon_ids), AddonDefinition.is_active.is_(True))
        )
        def_result = await session.execute(def_stmt.options(selectinload(AddonDefinition.order_addons)))
        definitions = {item.addon_id: item for item in def_result.scalars().all()}
        if len(definitions) != len(addon_ids):
            raise ValueError("Invalid or inactive addon provided")
    else:
        definitions = {}

    await session.execute(delete(OrderAddon).where(OrderAddon.order_id == order_id))

    new_addons: list[OrderAddon] = []
    for selection in selections:
        definition = definitions.get(selection.addon_id)
        addon = OrderAddon(
            order_id=order_id,
            addon_id=selection.addon_id,
            qty=selection.qty,
            unit_price_cents_snapshot=definition.price_cents,
            minutes_snapshot=definition.default_minutes,
        )
        addon.definition = definition
        session.add(addon)
        new_addons.append(addon)

    await session.flush()
    if new_addons:
        await session.refresh(order)

    base_planned = order.planned_minutes if order.planned_minutes is not None else order.duration_minutes
    base_planned = max(base_planned - previous_minutes, 0)
    additional_minutes = sum(item.minutes_snapshot * item.qty for item in new_addons)
    order.planned_minutes = base_planned + additional_minutes

    if order.planned_minutes and order.planned_minutes < order.duration_minutes:
        order.planned_minutes = order.duration_minutes

    await session.flush()
    return new_addons


async def list_order_addons(session: AsyncSession, order_id: str) -> list[OrderAddon]:
    stmt: Select[OrderAddon] = (
        select(OrderAddon)
        .where(OrderAddon.order_id == order_id)
        .options(selectinload(OrderAddon.definition))
        .order_by(OrderAddon.order_addon_id)
    )
    result = await session.execute(stmt)
    return result.scalars().all()


def _infer_tax_rate(lead: Lead | None) -> Decimal | None:
    snapshot = getattr(lead, "estimate_snapshot", None) or {}
    for key in ("tax_rate", "gst_rate"):
        if key in snapshot:
            try:
                return Decimal(str(snapshot[key]))
            except (InvalidOperation, TypeError, ValueError):
                continue

    subtotal = snapshot.get("subtotal_cents") or snapshot.get("price_cents")
    tax_cents = snapshot.get("tax_cents")
    if subtotal and tax_cents:
        try:
            subtotal_decimal = Decimal(str(subtotal))
            if subtotal_decimal > 0:
                return Decimal(str(tax_cents)) / subtotal_decimal
        except (InvalidOperation, TypeError, ValueError):
            return None
    return None


async def addon_invoice_items_for_order(
    session: AsyncSession, order_id: str
) -> list[InvoiceItemCreate]:
    addons = await list_order_addons(session, order_id)
    booking_result = await session.execute(
        select(Booking).options(selectinload(Booking.lead)).where(Booking.booking_id == order_id)
    )
    booking = booking_result.scalar_one_or_none()
    tax_rate = _infer_tax_rate(getattr(booking, "lead", None)) if booking else None
    items: list[InvoiceItemCreate] = []
    for addon in addons:
        name = addon.definition.name if addon.definition else f"Addon {addon.addon_id}"
        items.append(
            InvoiceItemCreate(
                description=name,
                qty=addon.qty,
                unit_price_cents=addon.unit_price_cents_snapshot,
                tax_rate=tax_rate,
            )
        )
    return items


async def addon_report(
    session: AsyncSession, *, start: datetime | None = None, end: datetime | None = None
) -> list[schemas.AddonReportItem]:
    stmt = (
        select(
            AddonDefinition.addon_id,
            AddonDefinition.code,
            AddonDefinition.name,
            func.coalesce(func.sum(OrderAddon.qty), 0).label("total_qty"),
            func.coalesce(func.sum(OrderAddon.qty * OrderAddon.unit_price_cents_snapshot), 0).label(
                "revenue_cents"
            ),
        )
        .join(OrderAddon, OrderAddon.addon_id == AddonDefinition.addon_id)
        .join(Booking, Booking.booking_id == OrderAddon.order_id)
        .group_by(AddonDefinition.addon_id, AddonDefinition.code, AddonDefinition.name)
        .order_by(AddonDefinition.name)
    )

    conditions = []
    if start:
        conditions.append(Booking.starts_at >= start)
    if end:
        conditions.append(Booking.starts_at <= end)
    if conditions:
        stmt = stmt.where(and_(*conditions))

    result = await session.execute(stmt)
    rows = result.all()
    return [
        schemas.AddonReportItem(
            addon_id=row.addon_id,
            code=row.code,
            name=row.name,
            total_qty=int(row.total_qty or 0),
            revenue_cents=int(row.revenue_cents or 0),
        )
        for row in rows
    ]
