import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.bot.analytics.metrics import BotMetrics
from app.bot.faq.engine import format_matches, match_faq
from app.bot.fsm import BotFsm
from app.bot.handoff.engine import build_case_payload, evaluate_handoff
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
    if "faq" in request.text.lower() and nlu_result.intent != Intent.complaint:
        nlu_result = nlu_result.model_copy(
            update={"intent": Intent.faq, "reasons": [*nlu_result.reasons, "explicit faq keyword"]}
        )
    fsm = BotFsm(conversation.state)
    fsm_reply = fsm.handle(request.text, nlu_result)
    updated_state = fsm.state
    fsm_step = updated_state.fsm_step or _fsm_step_for_intent(nlu_result.intent)

    faq_matches = None
    bot_text = fsm_reply.text or (
        "Thanks! I noted your request. "
        "I'll keep gathering details so we can prepare the right follow-up."
    )
    quick_replies = list(fsm_reply.quick_replies)
    progress = fsm_reply.progress
    summary = fsm_reply.summary

    if nlu_result.intent == Intent.faq:
        faq_matches = match_faq(request.text)
        if faq_matches:
            bot_text = format_matches(faq_matches)
            quick_replies = ["Book a cleaning", "Talk to a human"]
            progress = None
        else:
            bot_text = (
                "I want to make sure you get the right answer. "
                "I'm connecting you to a human teammate."
            )
            quick_replies = ["Talk to a human"]

    decision = evaluate_handoff(
        intent_result=nlu_result,
        fsm_reply=fsm_reply,
        message_text=request.text,
        faq_matches=faq_matches,
    )

    if decision.should_handoff:
        fsm_step = FsmStep.handoff_check
        updated_state.fsm_step = fsm_step
        handoff_note = (
            "I'm looping in a human specialist now. "
            "They will review this conversation and follow up."
        )
        bot_text = f"{bot_text}\n\n{handoff_note}" if bot_text else handoff_note
        quick_replies = []

    fsm_step_value = fsm_step.value if hasattr(fsm_step, "value") else fsm_step
    await store.update_state(request.conversation_id, updated_state)

    metadata = {**fsm_reply.metadata, "quickReplies": quick_replies, "progress": progress, "summary": summary}
    bot_payload = MessagePayload(
        role=MessageRole.bot,
        text=bot_text,
        intent=nlu_result.intent,
        confidence=nlu_result.confidence,
        extracted_entities=nlu_result.entities.model_dump(exclude_none=True, by_alias=True),
        reasons=nlu_result.reasons,
        metadata=metadata,
    )
    await store.append_message(request.conversation_id, bot_payload)

    if decision.should_handoff:
        await store.mark_handed_off(request.conversation_id)
        messages = await store.list_messages(request.conversation_id)
        case_payload = build_case_payload(
            reason=decision.reason or "handoff",
            decision=decision,
            conversation_id=request.conversation_id,
            messages=messages,
            intent_result=nlu_result,
            fsm_reply=fsm_reply,
        )
        await store.create_case(case_payload)
        metrics: Optional[BotMetrics] = getattr(http_request.app.state, "bot_metrics", None)
        if metrics:
            metrics.record_handoff(decision.reason or "handoff", str(fsm_step_value))

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

        # NLU explainability (keep small enough)
        "reasons": nlu_result.reasons,
        "entities": nlu_result.entities.model_dump(exclude_none=True, by_alias=True),

        # Pricing summary (flat, safe)
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
            quick_replies=quick_replies,
            progress=progress,
            summary=summary,
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
