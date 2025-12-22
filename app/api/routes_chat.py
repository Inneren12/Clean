import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.chat.models import ChatTurnRequest, ChatTurnResponse, ParsedFields
from app.domain.chat.state_machine import handle_turn
from app.domain.leads.db_models import ChatSession
from app.domain.pricing.config_loader import PricingConfig
from app.dependencies import get_pricing_config, get_db_session

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/v1/chat/turn", response_model=ChatTurnResponse)
async def chat_turn(
    request: ChatTurnRequest,
    pricing_config: PricingConfig = Depends(get_pricing_config),
    session: AsyncSession = Depends(get_db_session),
) -> ChatTurnResponse:
    result = await session.execute(
        select(ChatSession).where(ChatSession.session_id == request.session_id)
    )
    existing = result.scalar_one_or_none()
    parsed_state = ParsedFields(**existing.state_json) if existing else None
    response, merged = handle_turn(request, parsed_state, pricing_config)
    state_payload = merged.model_dump(mode="json")
    if existing:
        existing.state_json = state_payload
        existing.brand = request.brand
    else:
        session.add(
            ChatSession(
                session_id=request.session_id,
                brand=request.brand,
                state_json=state_payload,
            )
        )
    await session.commit()

    logger.info(
        "chat_turn",
        extra={"extra": {"session_id": request.session_id, "intent": response.intent.value}},
    )
    return response
