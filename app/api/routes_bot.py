import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.bot.faq.formatter import clarification_prompt, format_matches
from app.bot.faq.matcher import match_faq
from app.bot.fsm import BotFsm
from app.bot.handoff.case_builder import build_case_payload
from app.bot.handoff.decision import evaluate_handoff
from app.bot.handoff.reasons import HandoffReason
from app.bot.handoff.metrics import metrics
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
    ConversationStatus,
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
FLOW_INTENTS = {Intent.booking, Intent.price, Intent.scope, Intent.reschedule}


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
    normalized_text = request.text.strip().lower()
    word_count = len([w for w in normalized_text.split(" ") if w])
    is_trivial_message = len(normalized_text) <= 6 or word_count <= 2

    if updated_state.current_intent not in FLOW_INTENTS and is_trivial_message:
        fallback_step = _fsm_step_for_intent(nlu_result.intent)
        updated_state = ConversationState(
            current_intent=updated_state.current_intent or nlu_result.intent,
            fsm_step=fallback_step,
            filled_fields=fsm.state.filled_fields,
            confidence=nlu_result.confidence,
            last_estimate=fsm.state.last_estimate,
        )

    fsm_step_value = (
        updated_state.fsm_step.value
        if hasattr(updated_state.fsm_step, "value")
        else updated_state.fsm_step
    )
    step_str = str(fsm_step_value) if fsm_step_value else ""
    fsm_is_flow = updated_state.current_intent in FLOW_INTENTS and (
        step_str.startswith("ask_") or step_str == "confirm_lead"
    )
    # A2: Detect explicit FAQ requests (faq:, "faq", or "faq ")
    text_lower = request.text.lower().strip()
    faq_requested = (
        text_lower.startswith("faq:")
        or text_lower.startswith("faq ")
        or text_lower == "faq"
    )

    # A3: Only compute faq_matches when FAQ explicitly requested AND not in active flow
    faq_matches = match_faq(request.text) if (faq_requested and not fsm_is_flow) else []

    # Base reply from FSM
    bot_text = fsm_reply.text
    quick_replies = list(fsm_reply.quick_replies)

    # FAQ overlay is allowed ONLY if explicitly requested AND not in active flow
    if faq_requested and not fsm_is_flow:
        if faq_matches:
            bot_text = format_matches(faq_matches)
            quick_replies = []
        else:
            bot_text, quick_replies = clarification_prompt()

    # Handoff decision (real implementation returns HandoffDecision)
    decision = evaluate_handoff(
        intent_result=nlu_result,
        fsm_reply=fsm_reply,
        message_text=request.text,
        faq_matches=faq_matches,
        current_intent=updated_state.current_intent,
    )

    # Optional clarification overlay from decision, but NEVER mid-flow and never when FAQ explicitly requested
    if (
        decision.suggested_action == "clarify"
        and not faq_requested
        and not decision.should_handoff
        and not fsm_is_flow
    ):
        bot_text, quick_replies = clarification_prompt()

    # Move to handoff_check step when decision says handoff (unless already there)
    if decision.should_handoff and updated_state.fsm_step != FsmStep.handoff_check:
        updated_state = ConversationState(
            current_intent=nlu_result.intent,
            fsm_step=FsmStep.handoff_check,
            filled_fields=fsm.state.filled_fields,
            confidence=nlu_result.confidence,
            last_estimate=fsm.state.last_estimate,
        )

    # If handing off, override bot text and remove quick replies
    if decision.should_handoff:
        bot_text = "I'll connect you to a human right away to help with this."
        quick_replies = []

    # Persist updated FSM state + status
    fsm_step = updated_state.fsm_step or _fsm_step_for_intent(nlu_result.intent)
    fsm_step_value = fsm_step.value if hasattr(fsm_step, "value") else fsm_step

    await store.update_state(
        request.conversation_id,
        updated_state,
        status=ConversationStatus.handed_off if decision.should_handoff else None,
    )

    # Build metadata for frontend
    metadata = {**fsm_reply.metadata, "quickReplies": quick_replies}
    if decision.should_handoff:
        metadata["handoff"] = {
            "reason": decision.reason.value if isinstance(decision.reason, HandoffReason) else decision.reason,
            "summary": decision.summary,
        }

    # Ensure bot_text always exists
    if not bot_text:
        bot_text = "Thanks! One moment while I continue."

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
        conversation = await store.get_conversation(request.conversation_id)
        messages = await store.list_messages(request.conversation_id)
        case_payload = build_case_payload(
            decision=decision,
            conversation=conversation,
            messages=messages,
            fsm_reply=fsm_reply,
            intent_result=nlu_result,
        )
        await store.create_case(case_payload)
        metrics.record(decision.reason or HandoffReason.human_requested, updated_state.fsm_step)

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
            "handoff_reason": decision.reason.value if isinstance(decision.reason, HandoffReason) else decision.reason,
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
