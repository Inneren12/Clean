from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.saas.db_models import OrganizationBilling, OrganizationUsageEvent
from app.domain.saas.plans import Plan, get_plan


ACTIVE_SUBSCRIPTION_STATES = {"active", "trialing", "past_due"}


def normalize_subscription_status(status: str | None) -> str:
    normalized = str(status).lower() if status else "incomplete"
    return normalized


async def get_or_create_billing(session: AsyncSession, org_id: uuid.UUID) -> OrganizationBilling:
    stmt = sa.select(OrganizationBilling).where(OrganizationBilling.org_id == org_id).with_for_update()
    result = await session.execute(stmt)
    billing = result.scalar_one_or_none()
    if billing:
        return billing

    bind = session.get_bind()
    dialect = getattr(getattr(bind, "dialect", None), "name", "") if bind else ""
    if dialect == "sqlite":
        insert_stmt = (
            sqlite_insert(OrganizationBilling)
            .values(org_id=org_id, plan_id="free", status="inactive")
            .on_conflict_do_nothing(index_elements=[OrganizationBilling.org_id])
        )
    else:
        insert_stmt = (
            pg_insert(OrganizationBilling)
            .values(org_id=org_id, plan_id="free", status="inactive")
            .on_conflict_do_nothing(index_elements=[OrganizationBilling.org_id])
        )

    await session.execute(insert_stmt)
    result = await session.execute(stmt)
    return result.scalar_one()


async def set_plan(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    plan_id: str,
    status: str,
    stripe_customer_id: str | None = None,
    stripe_subscription_id: str | None = None,
    current_period_end: dt.datetime | None = None,
) -> OrganizationBilling:
    billing = await get_or_create_billing(session, org_id)
    billing.plan_id = plan_id
    billing.status = normalize_subscription_status(status)
    billing.stripe_customer_id = stripe_customer_id or billing.stripe_customer_id
    billing.stripe_subscription_id = stripe_subscription_id or billing.stripe_subscription_id
    billing.current_period_end = current_period_end
    await session.flush()
    return billing


async def get_billing_by_customer(
    session: AsyncSession, stripe_customer_id: str
) -> OrganizationBilling | None:
    stmt = sa.select(OrganizationBilling).where(
        OrganizationBilling.stripe_customer_id == stripe_customer_id
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def update_from_subscription_payload(session: AsyncSession, payload: Any) -> OrganizationBilling | None:
    data_object = getattr(payload, "data", None)
    if data_object is None and isinstance(payload, dict):
        data_object = payload.get("data")

    subscription = getattr(data_object, "object", None) if data_object is not None else None
    if subscription is None and isinstance(data_object, dict):
        subscription = data_object.get("object")
    subscription = subscription or {}

    metadata = subscription.get("metadata") if isinstance(subscription, dict) else getattr(subscription, "metadata", None)
    metadata = metadata or {}
    org_id_raw = metadata.get("org_id") if isinstance(metadata, dict) else None
    plan_id = metadata.get("plan_id") if isinstance(metadata, dict) else None
    stripe_customer_id = None
    if isinstance(subscription, dict):
        stripe_customer_id = subscription.get("customer")
        subscription_id = subscription.get("id")
        period_end_ts = subscription.get("current_period_end")
    else:
        stripe_customer_id = getattr(subscription, "customer", None)
        subscription_id = getattr(subscription, "id", None)
        period_end_ts = getattr(getattr(subscription, "current_period_end", None), "__int__", lambda: None)()

    if not org_id_raw or not subscription_id:
        return None

    try:
        org_id = uuid.UUID(str(org_id_raw))
    except ValueError:
        return None

    current_period_end = None
    if period_end_ts:
        current_period_end = dt.datetime.fromtimestamp(int(period_end_ts), tz=dt.timezone.utc)

    status_raw = getattr(subscription, "status", None) or subscription.get("status") if isinstance(subscription, dict) else None
    status = normalize_subscription_status(status_raw)
    resolved_plan = get_plan(plan_id)
    billing = await set_plan(
        session,
        org_id,
        plan_id=resolved_plan.plan_id,
        status=status,
        stripe_customer_id=stripe_customer_id if stripe_customer_id else None,
        stripe_subscription_id=subscription_id,
        current_period_end=current_period_end,
    )
    return billing


async def get_current_plan(session: AsyncSession, org_id: uuid.UUID) -> Plan:
    stmt = sa.select(OrganizationBilling).where(OrganizationBilling.org_id == org_id)
    result = await session.execute(stmt)
    billing = result.scalar_one_or_none()
    if not billing:
        return get_plan("free")
    if billing.status not in ACTIVE_SUBSCRIPTION_STATES:
        return get_plan("free")
    return get_plan(billing.plan_id)


async def record_usage_event(
    session: AsyncSession,
    org_id: uuid.UUID,
    metric: str,
    quantity: int = 1,
    resource_id: str | None = None,
) -> OrganizationUsageEvent:
    event = OrganizationUsageEvent(org_id=org_id, metric=metric, quantity=quantity, resource_id=resource_id)
    session.add(event)
    await session.flush()
    return event


async def usage_snapshot(session: AsyncSession, org_id: uuid.UUID) -> dict[str, int]:
    now = dt.datetime.now(tz=dt.timezone.utc)
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    workers_query = sa.select(sa.func.coalesce(sa.func.sum(OrganizationUsageEvent.quantity), 0)).where(
        OrganizationUsageEvent.org_id == org_id, OrganizationUsageEvent.metric == "worker_created"
    )
    bookings_query = sa.select(sa.func.count()).where(
        OrganizationUsageEvent.org_id == org_id,
        OrganizationUsageEvent.metric == "booking_created",
        OrganizationUsageEvent.created_at >= start_of_month,
    )
    storage_query = sa.select(sa.func.coalesce(sa.func.sum(OrganizationUsageEvent.quantity), 0)).where(
        OrganizationUsageEvent.org_id == org_id, OrganizationUsageEvent.metric == "storage_bytes"
    )

    workers = (await session.execute(workers_query)).scalar_one() or 0
    bookings = (await session.execute(bookings_query)).scalar_one() or 0
    storage = (await session.execute(storage_query)).scalar_one() or 0

    return {
        "workers": int(workers),
        "bookings_this_month": int(bookings),
        "storage_bytes": int(storage),
    }
