import logging
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.bot.fsm import BotFsm
from app.bot.nlu.engine import analyze_message
from app.bot.nlu.models import Intent
from app.dependencies import get_bot_store
from app.domain.bot.schemas import (
    BotReply,
    CasePayload,
    CaseRecord,
    ConversationCreate,
    ConversationRecord,
    ConversationState,
    FsmStep,
    LeadPayload,
    LeadRecord,
    MessageRecord,
    MessagePayload,
    MessageRequest,
    MessageResponse,
    MessageRole,
    SessionCreateRequest,
    SessionCreateResponse,
)
from app.infra.bot_store import BotStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


def _fsm_step_for_intent(intent: Intent) -> FsmStep:
    match intent:
        case Intent.booking | Intent.reschedule:
            return FsmStep.ask_service_type
        case Intent.price | Intent.scope:
            return FsmStep.ask_service_type
        case Intent.cancel | Intent.status:
            return FsmStep.routing
        case Intent.human | Intent.complaint:
            return FsmStep.handoff_check
        case _:
            return FsmStep.routing
@router.post("/bot/session", response_model=SessionCreateResponse, status_code=201)
async def create_session(request: SessionCreateRequest, store: BotStore = Depends(get_bot_store)) -> SessionCreateResponse:
    conversation = await store.create_conversation(
        ConversationCreate(
            channel=request.channel,
            user_id=request.user_id,
            anon_id=request.anon_id,
            state=ConversationState(),
        )
    )
    return SessionCreateResponse(
        conversation_id=conversation.conversation_id,
        status=conversation.status,
        state=conversation.state,
    )


@router.post("/bot/message", response_model=MessageResponse)
async def post_message(
    request: MessageRequest, http_request: Request, store: BotStore = Depends(get_bot_store)
) -> MessageResponse:
    conversation = await store.get_conversation(request.conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    user_message = MessagePayload(role=MessageRole.user, text=request.text)
    await store.append_message(request.conversation_id, user_message)

    nlu_result = analyze_message(request.text)
    fsm = BotFsm(conversation.state)
    fsm_reply = fsm.handle(request.text, nlu_result)
    updated_state = fsm.state
    fsm_step = updated_state.fsm_step or _fsm_step_for_intent(nlu_result.intent)
    fsm_step_value = fsm_step.value if hasattr(fsm_step, "value") else fsm_step
    await store.update_state(request.conversation_id, updated_state)

    # A1: Compute fsm_is_flow to safeguard against FAQ/clarification overlays
    # When fsm_is_flow is True, NEVER override FSM reply with FAQ/clarify prompts
    step_str = str(fsm_step_value) if fsm_step_value else ""
    fsm_is_flow = step_str.startswith("ask_") or step_str == "confirm_lead"

    # A2-A5: Safeguards for future FAQ/handoff integration:
    # - Only compute faq_matches when FAQ explicitly requested (faq:, faq command, etc.)
    # - Never apply FAQ/clarification overlays when fsm_is_flow is True
    # - Only handoff when decision.should_handoff == True, never during active flow
    # - Complaint/human intents always handoff and create case (handled by FSM)

    bot_text = fsm_reply.text or (
        "Thanks! I noted your request. "
        "I'll keep gathering details so we can prepare the right follow-up."
    )
    bot_payload = MessagePayload(
        role=MessageRole.bot,
        text=bot_text,
        intent=nlu_result.intent,
        confidence=nlu_result.confidence,
        extracted_entities=nlu_result.entities.model_dump(exclude_none=True, by_alias=True),
        reasons=nlu_result.reasons,
        metadata=fsm_reply.metadata,
    )
    await store.append_message(request.conversation_id, bot_payload)

    request_id = getattr(http_request.state, "request_id", None) if http_request else None
    estimate = fsm_reply.estimate
    logger.info(
        "intent_detected",
        extra={
            "request_id": request_id,
            "conversation_id": request.conversation_id,
            "intent": nlu_result.intent.value,
            "confidence": nlu_result.confidence,
            "fsm_step": fsm_step_value,
            "estimate_min": estimate.price_range_min if estimate else None,
            "estimate_max": estimate.price_range_max if estimate else None,
            "estimate_duration": estimate.duration_minutes if estimate else None,
        },
    )

    return MessageResponse(
        conversation_id=request.conversation_id,
        reply=BotReply(
            text=bot_text,
            intent=nlu_result.intent,
            confidence=nlu_result.confidence,
            state=updated_state,
            extracted_entities=nlu_result.entities.model_dump(exclude_none=True, by_alias=True),
            reasons=nlu_result.reasons,
            quick_replies=fsm_reply.quick_replies,
            progress=fsm_reply.progress,
            summary=fsm_reply.summary,
        ),
    )


@router.get("/bot/messages", response_model=list[MessageRecord])
async def list_messages(
    conversation_id: Optional[str] = Query(None, alias="conversationId"),
    legacy_conversation_id: Optional[str] = Query(None, alias="conversation_id"),
    store: BotStore = Depends(get_bot_store),
) -> list[MessageRecord]:
    conversation_key = conversation_id or legacy_conversation_id
    if not conversation_key:
        raise HTTPException(status_code=422, detail="conversationId is required")

    conversation = await store.get_conversation(conversation_key)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return await store.list_messages(conversation_key)


@router.get("/bot/session/{conversation_id}", response_model=ConversationRecord)
async def get_session(conversation_id: str, store: BotStore = Depends(get_bot_store)) -> ConversationRecord:
    conversation = await store.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@router.post("/leads", response_model=LeadRecord, status_code=201)
async def create_lead_from_conversation(
    payload: LeadPayload, http_request: Request, store: BotStore = Depends(get_bot_store)
) -> LeadRecord:
    if payload.source_conversation_id:
        conversation = await store.get_conversation(payload.source_conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        allowed_fields = set(LeadPayload.model_fields.keys())
        conversation_fields = {
            key: value for key, value in conversation.state.filled_fields.items() if key in allowed_fields
        }
        merged_payload = {**conversation_fields, **payload.model_dump(exclude_none=True)}
        merged_payload["source_conversation_id"] = conversation.conversation_id
        payload = LeadPayload(**merged_payload)

    lead = await store.create_lead(payload)
    logger.info(
        "lead_created",
        extra={
            "lead_id": lead.lead_id,
            "conversation_id": lead.source_conversation_id,
            "request_id": getattr(http_request.state, "request_id", None) if http_request else None,
        },
    )
    return lead


@router.post("/cases", response_model=CaseRecord, status_code=201)
async def create_case(
    payload: CasePayload, http_request: Request, store: BotStore = Depends(get_bot_store)
) -> CaseRecord:
    if payload.source_conversation_id:
        conversation = await store.get_conversation(payload.source_conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
    case = await store.create_case(payload)
    logger.info(
        "handoff_case",
        extra={
            "case_id": case.case_id,
            "conversation_id": case.source_conversation_id,
            "reason": case.reason,
            "request_id": getattr(http_request.state, "request_id", None) if http_request else None,
        },
    )
    return case
