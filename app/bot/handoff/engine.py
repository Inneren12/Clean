from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.bot.fsm.engine import FsmReply
from app.bot.nlu.models import Intent, IntentResult
from app.domain.bot.schemas import CasePayload, MessageRecord

logger = logging.getLogger(__name__)

HANDOFF_CONFIDENCE_THRESHOLD = 0.45
SCHEDULING_CONFLICT_KEYWORDS = ["not available", "no availability", "fully booked", "conflict", "busy"]


@dataclass
class HandoffDecision:
    should_handoff: bool
    reason: Optional[str] = None
    suggested_action: Optional[str] = None
    summary: Optional[str] = None


def _format_summary(summary: Dict[str, Any]) -> str:
    if not summary:
        return "Escalated with no structured summary captured."
    parts = [f"{key}: {value}" for key, value in summary.items() if value is not None]
    return "Escalated. Summary — " + "; ".join(parts)


def detect_scheduling_conflict(message_text: str, intent_result: IntentResult) -> bool:
    normalized = message_text.lower()
    if intent_result.intent == Intent.reschedule and intent_result.entities.time_window is None:
        return True
    return any(keyword in normalized for keyword in SCHEDULING_CONFLICT_KEYWORDS)


def evaluate_handoff(
    intent_result: IntentResult,
    fsm_reply: FsmReply,
    message_text: str,
    faq_matches: Optional[List[Any]] = None,
) -> HandoffDecision:
    if intent_result.intent == Intent.complaint:
        return HandoffDecision(
            should_handoff=True,
            reason="complaint",
            suggested_action="Call the customer to resolve the complaint and offer make-good options.",
            summary=_format_summary(fsm_reply.summary),
        )

    if intent_result.intent == Intent.human:
        return HandoffDecision(
            should_handoff=True,
            reason="human_requested",
            suggested_action="Connect the customer to an agent immediately and confirm their contact channel.",
            summary=_format_summary(fsm_reply.summary),
        )

    if detect_scheduling_conflict(message_text, intent_result):
        return HandoffDecision(
            should_handoff=True,
            reason="scheduling_conflict",
            suggested_action="Review availability and propose a concrete slot over phone or email.",
            summary=_format_summary(fsm_reply.summary),
        )

    if intent_result.confidence < HANDOFF_CONFIDENCE_THRESHOLD and not faq_matches:
        return HandoffDecision(
            should_handoff=True,
            reason="low_confidence",
            suggested_action="Review intent, reply manually, and confirm details.",
            summary=_format_summary(fsm_reply.summary),
        )

    if faq_matches is not None and not faq_matches:
        return HandoffDecision(
            should_handoff=True,
            reason="faq_unclear",
            suggested_action="Provide a direct answer and share a link to relevant help content.",
            summary=_format_summary(fsm_reply.summary),
        )

    return HandoffDecision(should_handoff=False)


def _normalize_messages(messages: List[MessageRecord]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for message in messages[-10:]:
        normalized.append(
            {
                "role": message.role,
                "text": message.text,
                "intent": getattr(message, "intent", None),
                "confidence": getattr(message, "confidence", None),
                "metadata": getattr(message, "metadata", None),
                "createdAt": getattr(message, "created_at", None),
            }
        )
    return normalized


def build_case_payload(
    reason: str,
    decision: HandoffDecision,
    conversation_id: str,
    messages: List[MessageRecord],
    intent_result: IntentResult,
    fsm_reply: FsmReply,
) -> CasePayload:
    payload = {
        "messages": _normalize_messages(messages),
        "entities": intent_result.entities.model_dump(exclude_none=True, by_alias=True),
        "summary": fsm_reply.summary,
        "suggestedNextAction": decision.suggested_action,
        "progress": fsm_reply.progress,
        "intent": intent_result.intent.value,
        "reason": reason,
    }
    summary = decision.summary or _format_summary(fsm_reply.summary)
    logger.info(
        "handoff_case_build",
        extra={"conversation_id": conversation_id, "reason": reason, "suggested_action": decision.suggested_action},
    )
    return CasePayload(
        reason=reason,
        summary=summary,
        payload=payload,
        source_conversation_id=conversation_id,
    )
