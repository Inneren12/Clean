import asyncio
import logging

import anyio
from fastapi import APIRouter, BackgroundTasks, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db_session
from app.domain.analytics.service import (
    EventType,
    estimated_duration_from_lead,
    estimated_revenue_from_lead,
    log_event,
)
from app.domain.leads.db_models import Lead
from app.domain.leads.schemas import LeadCreateRequest, LeadResponse
from app.domain.leads.statuses import LEAD_STATUS_NEW
from app.infra.export import export_lead_async
from app.infra.email import EmailAdapter

logger = logging.getLogger(__name__)

router = APIRouter()


def schedule_export(
    payload: dict,
    transport: object | None = None,
    resolver: object | None = None,
) -> None:
    async def _spawn() -> None:
        asyncio.create_task(export_lead_async(payload, transport=transport, resolver=resolver))

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        anyio.from_thread.run(_spawn)
    else:
        loop.create_task(export_lead_async(payload, transport=transport, resolver=resolver))


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
) -> LeadResponse:
    estimate_payload = request.estimate_snapshot.model_dump(mode="json")
    structured_inputs = request.structured_inputs.model_dump(mode="json")
    utm = request.utm
    utm_source = request.utm_source or (utm.utm_source if utm else None)
    utm_medium = request.utm_medium or (utm.utm_medium if utm else None)
    utm_campaign = request.utm_campaign or (utm.utm_campaign if utm else None)
    utm_term = request.utm_term or (utm.utm_term if utm else None)
    utm_content = request.utm_content or (utm.utm_content if utm else None)
    async with session.begin():
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
            pricing_config_version=request.estimate_snapshot.pricing_config_version,
            config_hash=request.estimate_snapshot.config_hash,
            status=LEAD_STATUS_NEW,
            utm_source=utm_source,
            utm_medium=utm_medium,
            utm_campaign=utm_campaign,
            utm_term=utm_term,
            utm_content=utm_content,
            referrer=request.referrer,
        )
        session.add(lead)
        await session.flush()
        await log_event(
            session,
            event_type=EventType.lead_created,
            lead=lead,
            estimated_revenue_cents=estimated_revenue_from_lead(lead),
            estimated_duration_minutes=estimated_duration_from_lead(lead),
        )
    await session.refresh(lead)

    logger.info("lead_created", extra={"extra": {"lead_id": lead.lead_id}})
    export_transport = getattr(http_request.app.state, "export_transport", None)
    export_resolver = getattr(http_request.app.state, "export_resolver", None)
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
            "created_at": lead.created_at.isoformat(),
        },
        export_transport,
        export_resolver,
    )
    background_tasks.add_task(schedule_email_request_received, email_adapter, lead)

    return LeadResponse(
        lead_id=lead.lead_id,
        next_step_text="Thanks! Our team will confirm your booking and follow up shortly.",
    )
