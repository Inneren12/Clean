import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.dependencies import get_bot_store
from app.domain.bot.schemas import (
    BotReply,
    CasePayload,
    CaseRecord,
    ConversationCreate,
    ConversationRecord,
    ConversationState,
    FsmStep,
    Intent,
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


def _detect_intent(text: str) -> tuple[Intent, float, FsmStep]:
    normalized = text.lower()
    if "price" in normalized or "quote" in normalized:
        return Intent.quote, 0.82, FsmStep.collecting_requirements
    if "complaint" in normalized or "issue" in normalized:
        return Intent.complaint, 0.72, FsmStep.handoff_check
    if "book" in normalized:
        return Intent.book, 0.78, FsmStep.scheduling
    return Intent.faq, 0.55, FsmStep.routing


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

    intent, confidence, fsm_step = _detect_intent(request.text)
    updated_state = ConversationState(
        current_intent=intent,
        fsm_step=fsm_step,
        filled_fields={**conversation.state.filled_fields, "last_message": request.text},
        confidence=confidence,
    )
    await store.update_state(request.conversation_id, updated_state)

    bot_text = (
        "Thanks! I noted your request. "
        "I'll keep gathering details so we can prepare the right follow-up."
    )
    bot_payload = MessagePayload(
        role=MessageRole.bot,
        text=bot_text,
        intent=intent,
        confidence=confidence,
        extracted_entities={},
    )
    await store.append_message(request.conversation_id, bot_payload)

    logger.info(
        "intent_detected",
        extra={
            "conversation_id": request.conversation_id,
            "intent": intent.value,
            "confidence": confidence,
            "fsm_step": fsm_step.value,
            "request_id": getattr(http_request.state, "request_id", None) if http_request else None,
        },
    )

    return MessageResponse(
        conversation_id=request.conversation_id,
        reply=BotReply(text=bot_text, intent=intent, confidence=confidence, state=updated_state),
    )


@router.get("/bot/messages", response_model=list[MessageRecord])
async def list_messages(conversation_id: str, store: BotStore = Depends(get_bot_store)) -> list[MessageRecord]:
    conversation = await store.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return await store.list_messages(conversation_id)


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
