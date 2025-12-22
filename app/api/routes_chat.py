import logging
from fastapi import APIRouter, Depends

from app.domain.chat.models import ChatTurnRequest, ChatTurnResponse, ParsedFields
from app.domain.chat.state_machine import handle_turn
from app.domain.pricing.config_loader import PricingConfig
from app.infra.db import InMemoryChatSessionStore
from app.dependencies import get_pricing_config, get_chat_store

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/v1/chat/turn", response_model=ChatTurnResponse)
async def chat_turn(
    request: ChatTurnRequest,
    pricing_config: PricingConfig = Depends(get_pricing_config),
    store: InMemoryChatSessionStore = Depends(get_chat_store),
) -> ChatTurnResponse:
    existing = store.get(request.session_id)
    parsed_state = ParsedFields(**existing.state) if existing else None
    response, merged = handle_turn(request, parsed_state, pricing_config)
    store.upsert(request.session_id, merged.model_dump())

    logger.info(
        "chat_turn",
        extra={"extra": {"session_id": request.session_id, "intent": response.intent.value}},
    )
    return response
