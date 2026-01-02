from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.saas_auth import require_saas_user, SaaSIdentity
from app.domain.saas import billing_service
from app.domain.saas.plans import get_plan
from app.infra import stripe_client as stripe_infra
from app.infra.db import get_db_session
from app.shared.circuit_breaker import CircuitBreakerOpenError
from app.settings import settings

router = APIRouter()


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


@router.post("/v1/billing/checkout", response_model=CheckoutResponse, status_code=status.HTTP_201_CREATED)
async def create_billing_checkout(
    payload: CheckoutRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
    identity: SaaSIdentity = Depends(require_saas_user),
) -> CheckoutResponse:
    plan = get_plan(payload.plan_id)
    stripe_client = stripe_infra.resolve_client(http_request.app.state)
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

    stripe_client = stripe_infra.resolve_client(http_request.app.state)
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
    plan = await billing_service.get_current_plan(session, identity.org_id)
    usage = await billing_service.usage_snapshot(session, identity.org_id)

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
    )
