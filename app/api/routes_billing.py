from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
import datetime as dt
import uuid

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_auth import AdminPermission
from app.api.saas_auth import SaaSIdentity, require_permissions, require_saas_user
from app.domain.saas import billing_service
from app.domain.saas.plans import get_plan
from app.infra import stripe_client as stripe_infra
from app.infra.db import get_db_session
from app.shared.circuit_breaker import CircuitBreakerOpenError
from app.settings import settings

router = APIRouter()


def _stripe_client(request: Request):
    if getattr(request.app.state, "stripe_client", None):
        return request.app.state.stripe_client
    services = getattr(request.app.state, "services", None)
    if services and getattr(services, "stripe_client", None):
        return services.stripe_client
    return stripe_infra.resolve_client(request.app.state)


class CheckoutRequest(BaseModel):
    plan_id: str


class CheckoutResponse(BaseModel):
    checkout_url: str
    provider: str = "stripe"


class BillingStatusResponse(BaseModel):
    plan_id: str
    plan_name: str
    limits: dict[str, Any]
    usage: dict[str, int]
    status: str
    current_period_end: str | None
    pause_reason_code: str | None = None
    resume_reason_code: str | None = None
    paused_at: str | None = None
    resumed_at: str | None = None


class UsageMetricResponse(BaseModel):
    value: int
    limit: int | None
    over_limit: bool
    recorded: int | None = None
    drift: int | None = None


class UsageReportResponse(BaseModel):
    org_id: uuid.UUID
    plan_id: str
    plan_name: str
    status: str
    current_period_end: str | None
    period_start: str
    period_end: str
    drift_detected: bool
    usage: dict[str, UsageMetricResponse]


class PauseRequest(BaseModel):
    reason_code: str | None = Field(default=None, max_length=64)


class ResumeRequest(BaseModel):
    reason_code: str | None = Field(default=None, max_length=64)


def _serialize_status(billing, usage: dict[str, int]) -> BillingStatusResponse:
    plan = get_plan(billing.plan_id)
    return BillingStatusResponse(
        plan_id=plan.plan_id,
        plan_name=plan.name,
        limits={
            "max_workers": plan.limits.max_workers,
            "max_bookings_per_month": plan.limits.max_bookings_per_month,
            "storage_gb": plan.limits.storage_gb,
        },
        usage=usage,
        status=billing.status,
        current_period_end=billing.current_period_end.isoformat() if billing.current_period_end else None,
        pause_reason_code=billing.pause_reason_code,
        resume_reason_code=billing.resume_reason_code,
        paused_at=billing.paused_at.isoformat() if billing.paused_at else None,
        resumed_at=billing.resumed_at.isoformat() if billing.resumed_at else None,
    )


@router.post("/v1/billing/checkout", response_model=CheckoutResponse, status_code=status.HTTP_201_CREATED)
async def create_billing_checkout(
    payload: CheckoutRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
    identity: SaaSIdentity = Depends(require_saas_user),
) -> CheckoutResponse:
    plan = get_plan(payload.plan_id)
    stripe_client = _stripe_client(http_request)
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Stripe not configured")

    billing = await billing_service.get_or_create_billing(session, identity.org_id)
    metadata = {"org_id": str(identity.org_id), "plan_id": plan.plan_id}
    try:
        checkout_session = await stripe_infra.call_stripe_client_method(
            stripe_client,
            "create_subscription_checkout_session",
            price_cents=plan.price_cents,
            currency=plan.currency,
            success_url=settings.stripe_billing_success_url,
            cancel_url=settings.stripe_billing_cancel_url,
            metadata=metadata,
            customer=billing.stripe_customer_id,
            price_id=plan.stripe_price_id,
            plan_name=plan.name,
        )
    except CircuitBreakerOpenError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe temporarily unavailable",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Stripe checkout unavailable") from exc

    checkout_url = getattr(checkout_session, "url", None) or checkout_session.get("url")
    billing.stripe_customer_id = (
        getattr(checkout_session, "customer", None)
        or checkout_session.get("customer")
        or billing.stripe_customer_id
    )
    await session.commit()
    return CheckoutResponse(checkout_url=checkout_url)


@router.get("/v1/billing/portal")
async def billing_portal(
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
    identity: SaaSIdentity = Depends(require_saas_user),
) -> dict[str, str]:
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Stripe not configured")

    billing = await billing_service.get_or_create_billing(session, identity.org_id)
    if not billing.stripe_customer_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Billing not initialized")

    stripe_client = _stripe_client(http_request)
    try:
        portal = await stripe_infra.call_stripe_client_method(
            stripe_client,
            "create_billing_portal_session",
            customer_id=billing.stripe_customer_id,
            return_url=settings.stripe_billing_portal_return_url,
        )
    except CircuitBreakerOpenError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe temporarily unavailable",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Stripe checkout unavailable") from exc
    url = getattr(portal, "url", None) or portal.get("url")
    return {"url": url, "provider": "stripe"}


@router.get("/v1/billing/status", response_model=BillingStatusResponse)
async def billing_status(
    session: AsyncSession = Depends(get_db_session),
    identity: SaaSIdentity = Depends(require_saas_user),
) -> BillingStatusResponse:
    billing = await billing_service.get_or_create_billing(session, identity.org_id)
    usage = await billing_service.usage_snapshot(session, identity.org_id)
    return _serialize_status(billing, usage)


def _parse_month(month: str | None) -> dt.datetime | None:
    if not month:
        return None
    try:
        parsed = dt.datetime.strptime(month, "%Y-%m")
        return parsed.replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid month format, expected YYYY-MM",
        ) from exc


@router.get("/v1/billing/usage/report", response_model=UsageReportResponse)
async def usage_report(
    month: str | None = None,
    session: AsyncSession = Depends(get_db_session),
    identity: SaaSIdentity = Depends(require_permissions(AdminPermission.FINANCE)),
) -> UsageReportResponse:
    reference_time = _parse_month(month)
    report = await billing_service.usage_report(
        session, identity.org_id, reference_time=reference_time
    )

    usage: dict[str, UsageMetricResponse] = {}
    for key, value in report["usage"].items():
        usage[key] = UsageMetricResponse(
            value=int(value),
            limit=int(report["limits"].get(key)) if report["limits"].get(key) is not None else None,
            over_limit=bool(report["overages"].get(key, False)),
            recorded=int(report["recorded_usage"].get(key, 0)),
            drift=int(report["drift"].get(key, 0)),
        )

    billing = report["billing"]
    return UsageReportResponse(
        org_id=identity.org_id,
        plan_id=report["plan"].plan_id,
        plan_name=report["plan"].name,
        status=billing.status,
        current_period_end=billing.current_period_end.isoformat() if billing.current_period_end else None,
        period_start=report["period_start"].isoformat(),
        period_end=report["period_end"].isoformat(),
        drift_detected=any(value.drift for value in usage.values()),
        usage=usage,
    )


@router.post("/v1/billing/pause", response_model=BillingStatusResponse)
async def pause_billing(
    payload: PauseRequest,
    session: AsyncSession = Depends(get_db_session),
    identity: SaaSIdentity = Depends(require_permissions(AdminPermission.FINANCE)),
) -> BillingStatusResponse:
    billing = await billing_service.pause_subscription(
        session, identity.org_id, reason_code=payload.reason_code
    )
    await session.commit()
    usage = await billing_service.usage_snapshot(session, identity.org_id)
    return _serialize_status(billing, usage)


@router.post("/v1/billing/resume", response_model=BillingStatusResponse)
async def resume_billing(
    payload: ResumeRequest,
    session: AsyncSession = Depends(get_db_session),
    identity: SaaSIdentity = Depends(require_permissions(AdminPermission.FINANCE)),
) -> BillingStatusResponse:
    billing = await billing_service.resume_subscription(
        session, identity.org_id, reason_code=payload.reason_code
    )
    await session.commit()
    usage = await billing_service.usage_snapshot(session, identity.org_id)
    return _serialize_status(billing, usage)
