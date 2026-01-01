import asyncio
import logging
import uuid

import anyio
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import entitlements
from app.dependencies import get_db_session, get_pricing_config
from app.domain.analytics.service import (
    EventType,
    estimated_duration_from_lead,
    estimated_revenue_from_lead,
    log_event,
)
from app.domain.leads.db_models import Lead
from app.domain.leads.service import ensure_unique_referral_code
from app.domain.leads.schemas import LeadCreateRequest, LeadResponse
from app.domain.leads.statuses import LEAD_STATUS_NEW
from app.domain.pricing.estimator import estimate
from app.domain.pricing.models import PricingConfig
from app.infra.captcha import verify_turnstile
from app.infra.export import export_lead_async
from app.infra.email import EmailAdapter

logger = logging.getLogger(__name__)

router = APIRouter()


def schedule_export(
    payload: dict,
    transport: object | None = None,
    resolver: object | None = None,
    session_factory: object | None = None,
) -> None:
    async def _spawn() -> None:
        asyncio.create_task(
            export_lead_async(
                payload,
                transport=transport,
                resolver=resolver,
                session_factory=session_factory,
            )
        )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        anyio.from_thread.run(_spawn)
    else:
        loop.create_task(
            export_lead_async(
                payload,
                transport=transport,
                resolver=resolver,
                session_factory=session_factory,
            )
        )


def schedule_email_request_received(adapter: EmailAdapter | None, lead: Lead) -> None:
    if adapter is None:
        return

    async def _spawn() -> None:
        try:
            await adapter.send_request_received(lead)
        except Exception:  # noqa: BLE001
            logger.warning("email_background_failed", extra={"extra": {"lead_id": lead.lead_id}})

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        anyio.from_thread.run(_spawn)
    else:
        loop.create_task(_spawn())


@router.post("/v1/leads", response_model=LeadResponse, status_code=status.HTTP_201_CREATED)
async def create_lead(
    request: LeadCreateRequest,
    background_tasks: BackgroundTasks,
    http_request: Request,
    session: AsyncSession = Depends(get_db_session),
    pricing_config: PricingConfig = Depends(get_pricing_config),
) -> LeadResponse:
    org_id = getattr(http_request.state, "org_id", None) or entitlements.resolve_org_id(http_request)
    turnstile_transport = getattr(http_request.app.state, "turnstile_transport", None)
    remote_ip = http_request.client.host if http_request.client else None
    captcha_ok = await verify_turnstile(request.captcha_token, remote_ip, transport=turnstile_transport)
    if not captcha_ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Captcha verification failed")

    # Check if estimate_snapshot is incomplete and needs server-side computation
    snapshot = request.estimate_snapshot
    needs_compute = (
        snapshot.pricing_config_id is None
        or snapshot.rate is None
        or snapshot.team_size is None
        or snapshot.time_on_site_hours is None
    )

    if needs_compute:
        # Recompute complete estimate from structured inputs
        try:
            full_estimate = estimate(request.structured_inputs, pricing_config)
            estimate_payload = full_estimate.model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "estimate_compute_failed_fallback_to_partial",
                extra={
                    "extra": {
                        "reason": type(exc).__name__,
                        "message": str(exc),
                    }
                },
            )
            # Fallback to whatever was provided
            estimate_payload = snapshot.model_dump(mode="json", exclude_none=True)
    else:
        # Use the provided complete estimate
        estimate_payload = snapshot.model_dump(mode="json")

    structured_inputs = request.structured_inputs.model_dump(mode="json")
    utm = request.utm
    utm_source = request.utm_source or (utm.utm_source if utm else None)
    utm_medium = request.utm_medium or (utm.utm_medium if utm else None)
    utm_campaign = request.utm_campaign or (utm.utm_campaign if utm else None)
    utm_term = request.utm_term or (utm.utm_term if utm else None)
    utm_content = request.utm_content or (utm.utm_content if utm else None)
    lead: Lead
    async with session.begin():
        referrer: Lead | None = None
        if request.referral_code:
            result = await session.execute(
                select(Lead).where(
                    Lead.referral_code == request.referral_code, Lead.org_id == org_id
                )
            )
            referrer = result.scalar_one_or_none()
            if referrer is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid referral code")

        # Extract pricing config version and hash (check top-level for backward compat)
        pricing_version = (
            request.pricing_config_version
            or snapshot.pricing_config_version
            or estimate_payload.get("pricing_config_version")
        )
        config_hash_value = (
            request.config_hash
            or snapshot.config_hash
            or estimate_payload.get("config_hash")
        )

        lead = Lead(
            name=request.name,
            phone=request.phone,
            email=request.email,
            postal_code=request.postal_code,
            address=request.address,
            preferred_dates=request.preferred_dates,
            access_notes=request.access_notes,
            parking=request.parking,
            pets=request.pets,
            allergies=request.allergies,
            notes=request.notes,
            structured_inputs=structured_inputs,
            estimate_snapshot=estimate_payload,
            pricing_config_version=pricing_version,
            config_hash=config_hash_value,
            status=LEAD_STATUS_NEW,
            utm_source=utm_source,
            utm_medium=utm_medium,
            utm_campaign=utm_campaign,
            utm_term=utm_term,
            utm_content=utm_content,
            referrer=request.referrer,
            referred_by_code=request.referral_code if referrer else None,
            org_id=uuid.UUID(str(org_id)),
        )
        session.add(lead)
        await ensure_unique_referral_code(session, lead)

        try:
            await log_event(
                session,
                event_type=EventType.lead_created,
                lead=lead,
                estimated_revenue_cents=estimated_revenue_from_lead(lead),
                estimated_duration_minutes=estimated_duration_from_lead(lead),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "analytics_log_failed",
                extra={
                    "extra": {
                        "event_type": "lead_created",
                        "lead_id": lead.lead_id,
                        "reason": type(exc).__name__,
                    }
                },
            )
    await session.refresh(lead)

    logger.info("lead_created", extra={"extra": {"lead_id": lead.lead_id}})
    export_transport = getattr(http_request.app.state, "export_transport", None)
    export_resolver = getattr(http_request.app.state, "export_resolver", None)
    export_session_factory = getattr(http_request.app.state, "db_session_factory", None)
    email_adapter: EmailAdapter | None = getattr(http_request.app.state, "email_adapter", None)
    background_tasks.add_task(
        schedule_export,
        {
            "lead_id": lead.lead_id,
            "name": lead.name,
            "phone": lead.phone,
            "email": lead.email,
            "postal_code": lead.postal_code,
            "address": lead.address,
            "preferred_dates": lead.preferred_dates,
            "access_notes": lead.access_notes,
            "parking": lead.parking,
            "pets": lead.pets,
            "allergies": lead.allergies,
            "notes": lead.notes,
            "structured_inputs": lead.structured_inputs,
            "estimate_snapshot": lead.estimate_snapshot,
            "pricing_config_version": lead.pricing_config_version,
            "config_hash": lead.config_hash,
            "status": lead.status,
            "utm_source": lead.utm_source,
            "utm_medium": lead.utm_medium,
            "utm_campaign": lead.utm_campaign,
            "utm_term": lead.utm_term,
            "utm_content": lead.utm_content,
            "referrer": lead.referrer,
            "referral_code": lead.referral_code,
            "referred_by_code": lead.referred_by_code,
            "created_at": lead.created_at.isoformat(),
        },
        export_transport,
        export_resolver,
        export_session_factory,
    )
    background_tasks.add_task(schedule_email_request_received, email_adapter, lead)

    return LeadResponse(
        lead_id=lead.lead_id,
        next_step_text="Thanks! Our team will confirm your booking and follow up shortly.",
        referral_code=lead.referral_code,
    )
