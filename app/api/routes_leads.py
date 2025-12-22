import logging

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db_session
from app.domain.leads.db_models import Lead
from app.domain.leads.schemas import LeadCreateRequest, LeadResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/v1/leads", response_model=LeadResponse, status_code=status.HTTP_201_CREATED)
async def create_lead(
    request: LeadCreateRequest,
    session: AsyncSession = Depends(get_db_session),
) -> LeadResponse:
    estimate_payload = request.estimate_snapshot.model_dump(mode="json")
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
        utm_source=request.utm_source,
        utm_medium=request.utm_medium,
        utm_campaign=request.utm_campaign,
        utm_term=request.utm_term,
        utm_content=request.utm_content,
        referrer=request.referrer,
    )
    session.add(lead)
    await session.commit()
    await session.refresh(lead)

    logger.info("lead_created", extra={"extra": {"lead_id": lead.lead_id}})

    return LeadResponse(
        lead_id=lead.lead_id,
        next_step_text="Thanks! Our team will confirm your booking and follow up shortly.",
    )
