import logging

from fastapi import APIRouter, BackgroundTasks, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db_session
from app.domain.leads.db_models import Lead
from app.domain.leads.schemas import LeadCreateRequest, LeadResponse
from app.infra.export import export_lead

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/v1/leads", response_model=LeadResponse, status_code=status.HTTP_201_CREATED)
async def create_lead(
    request: LeadCreateRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db_session),
) -> LeadResponse:
    estimate_payload = request.estimate_snapshot.model_dump(mode="json")
    utm = request.utm
    utm_source = request.utm_source or (utm.utm_source if utm else None)
    utm_medium = request.utm_medium or (utm.utm_medium if utm else None)
    utm_campaign = request.utm_campaign or (utm.utm_campaign if utm else None)
    utm_term = request.utm_term or (utm.utm_term if utm else None)
    utm_content = request.utm_content or (utm.utm_content if utm else None)
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
        structured_inputs=request.structured_inputs,
        estimate_snapshot=estimate_payload,
        pricing_config_version=request.estimate_snapshot.pricing_config_version,
        config_hash=request.estimate_snapshot.config_hash,
        utm_source=utm_source,
        utm_medium=utm_medium,
        utm_campaign=utm_campaign,
        utm_term=utm_term,
        utm_content=utm_content,
        referrer=request.referrer,
    )
    session.add(lead)
    await session.commit()
    await session.refresh(lead)

    logger.info("lead_created", extra={"extra": {"lead_id": lead.lead_id}})
    background_tasks.add_task(
        export_lead,
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
            "utm_source": lead.utm_source,
            "utm_medium": lead.utm_medium,
            "utm_campaign": lead.utm_campaign,
            "utm_term": lead.utm_term,
            "utm_content": lead.utm_content,
            "referrer": lead.referrer,
            "created_at": lead.created_at.isoformat(),
        },
    )

    return LeadResponse(
        lead_id=lead.lead_id,
        next_step_text="Thanks! Our team will confirm your booking and follow up shortly.",
    )
