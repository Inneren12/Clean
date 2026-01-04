from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.bookings.db_models import Booking, OrderPhoto
from app.domain.saas.db_models import OrganizationBilling, OrganizationUsageEvent
from app.domain.saas.plans import Plan, get_plan
from app.domain.workers.db_models import Worker


ACTIVE_SUBSCRIPTION_STATES = {"active", "trialing", "past_due"}


def normalize_subscription_status(status: str | None) -> str:
    normalized = str(status).lower() if status else "incomplete"
    return normalized


def _now() -> dt.datetime:
    now = dt.datetime.now(tz=dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    return now


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


def _normalize_reason(reason_code: str | None) -> str | None:
    if reason_code is None:
        return None
    trimmed = str(reason_code).strip()
    return trimmed.upper() if trimmed else None


async def pause_subscription(
    session: AsyncSession, org_id: uuid.UUID, *, reason_code: str | None = None
) -> OrganizationBilling:
    billing = await get_or_create_billing(session, org_id)
    billing.status = "paused"
    billing.pause_reason_code = _normalize_reason(reason_code)
    billing.resume_reason_code = None
    billing.paused_at = _now()
    billing.resumed_at = None
    await session.flush()
    return billing


async def resume_subscription(
    session: AsyncSession, org_id: uuid.UUID, *, reason_code: str | None = None
) -> OrganizationBilling:
    billing = await get_or_create_billing(session, org_id)
    billing.status = "active"
    billing.resume_reason_code = _normalize_reason(reason_code)
    billing.resumed_at = _now()
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


async def usage_snapshot(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    reference_time: dt.datetime | None = None,
    include_recorded_usage: bool = True,
) -> dict[str, int]:
    start_of_month, end_of_month = _month_window(reference_time)

    workers_query = sa.select(sa.func.count()).select_from(Worker).where(
        Worker.org_id == org_id, Worker.is_active.is_(True)
    )
    bookings_query = sa.select(sa.func.count()).select_from(Booking).where(
        Booking.org_id == org_id,
        Booking.created_at >= start_of_month,
        Booking.created_at < end_of_month,
    )
    storage_query = sa.select(sa.func.coalesce(sa.func.sum(OrderPhoto.size_bytes), 0)).where(
        OrderPhoto.org_id == org_id
    )

    workers = int((await session.execute(workers_query)).scalar_one() or 0)
    bookings = int((await session.execute(bookings_query)).scalar_one() or 0)
    storage = int((await session.execute(storage_query)).scalar_one() or 0)

    usage = {
        "workers": workers,
        "bookings_this_month": bookings,
        "storage_bytes": storage,
    }

    if include_recorded_usage:
        recorded = await usage_event_snapshot(session, org_id, reference_time=reference_time)
        usage = {key: max(value, recorded.get(key, 0)) for key, value in usage.items()}

    return usage


def _month_window(reference_time: dt.datetime | None = None) -> tuple[dt.datetime, dt.datetime]:
    now = reference_time or dt.datetime.now(tz=dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start_of_month.month == 12:
        end_of_month = start_of_month.replace(year=start_of_month.year + 1, month=1)
    else:
        end_of_month = start_of_month.replace(month=start_of_month.month + 1)
    return start_of_month, end_of_month


async def usage_event_snapshot(
    session: AsyncSession, org_id: uuid.UUID, *, reference_time: dt.datetime | None = None
) -> dict[str, int]:
    start_of_month, end_of_month = _month_window(reference_time)

    workers_query = sa.select(sa.func.coalesce(sa.func.sum(OrganizationUsageEvent.quantity), 0)).where(
        OrganizationUsageEvent.org_id == org_id, OrganizationUsageEvent.metric == "worker_created"
    )
    bookings_query = sa.select(sa.func.count()).where(
        OrganizationUsageEvent.org_id == org_id,
        OrganizationUsageEvent.metric == "booking_created",
        OrganizationUsageEvent.created_at >= start_of_month,
        OrganizationUsageEvent.created_at < end_of_month,
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


def _drift_delta(recorded: dict[str, int], computed: dict[str, int]) -> dict[str, int]:
    deltas: dict[str, int] = {}
    keys: set[str] = set(recorded.keys()) | set(computed.keys())
    for key in keys:
        deltas[key] = int(recorded.get(key, 0) - computed.get(key, 0))
    return deltas


async def usage_report(
    session: AsyncSession, org_id: uuid.UUID, *, reference_time: dt.datetime | None = None
) -> dict[str, Any]:
    billing = await get_or_create_billing(session, org_id)
    plan = await get_current_plan(session, org_id)
    start_of_month, end_of_month = _month_window(reference_time)

    computed_usage = await usage_snapshot(
        session, org_id, reference_time=reference_time, include_recorded_usage=False
    )
    recorded_usage = await usage_event_snapshot(session, org_id, reference_time=reference_time)
    drift = _drift_delta(recorded_usage, computed_usage)

    limits = {
        "workers": plan.limits.max_workers,
        "bookings_this_month": plan.limits.max_bookings_per_month,
        "storage_bytes": plan.limits.storage_gb * 1024 * 1024 * 1024,
    }

    overages = {
        key: computed_usage.get(key, 0) > limits[key] if key in limits else False
        for key in computed_usage
    }

    return {
        "plan": plan,
        "billing": billing,
        "period_start": start_of_month,
        "period_end": end_of_month,
        "usage": computed_usage,
        "recorded_usage": recorded_usage,
        "drift": drift,
        "overages": overages,
        "limits": limits,
    }
