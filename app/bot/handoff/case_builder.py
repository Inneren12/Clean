from __future__ import annotations

from typing import List

from app.bot.fsm.engine import FsmReply
from app.bot.handoff.decision import HandoffDecision
from app.bot.handoff.reasons import HandoffReason
from app.bot.nlu.models import IntentResult
from app.domain.bot.schemas import CasePayload, ConversationRecord, MessageRecord, FsmStep


def _serialize_messages(messages: List[MessageRecord]) -> list[dict]:
    return [
        {
            "role": message.role,
            "text": message.text,
            "ts": message.created_at,
            "intent": message.intent,
            "confidence": message.confidence,
            "metadata": message.metadata,
        }
        for message in messages
    ]


def build_case_payload(
    *,
    decision: HandoffDecision,
    conversation: ConversationRecord,
    messages: List[MessageRecord],
    fsm_reply: FsmReply,
    intent_result: IntentResult,
) -> CasePayload:
    step_value = fsm_reply.step.value if isinstance(fsm_reply.step, FsmStep) else fsm_reply.step
    payload = {
        "conversation": {
            "conversation_id": conversation.conversation_id,
            "channel": conversation.channel,
            "status": conversation.status,
            "state": conversation.state.model_dump(mode="json"),
        },
        "messages": _serialize_messages(messages[-10:]),
        "entities": intent_result.entities.model_dump(exclude_none=True, by_alias=True),
        "handoff": {
            "reason": HandoffReason.stable_reason(decision.reason.value if decision.reason else None),
            "summary": decision.summary,
            "suggested_action": decision.suggested_action,
        },
        "fsm": {
            "step": step_value,
            "progress": fsm_reply.progress,
            "summary": fsm_reply.summary,
        },
    }

    return CasePayload(
        reason=HandoffReason.stable_reason(decision.reason.value if decision.reason else None),
        summary=decision.summary or "Escalated to human agent",
        payload=payload,
        source_conversation_id=conversation.conversation_id,
    )
