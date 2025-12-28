from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.bot.fsm.engine import FsmReply
from app.bot.nlu.models import Intent, IntentResult
from app.domain.bot.schemas import CasePayload, MessageRecord, FsmStep

logger = logging.getLogger(__name__)

HANDOFF_CONFIDENCE_THRESHOLD = 0.3
LOW_CONFIDENCE_DEEP_THRESHOLD = 0.15


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


def detect_scheduling_conflict(intent_result: IntentResult) -> bool:
    return intent_result.intent == Intent.reschedule and intent_result.entities.time_window is None


def _is_short_or_greeting(message_text: str) -> bool:
    normalized = message_text.strip().lower()
    if not normalized:
        return True

    greetings = {"hi", "hey", "hello", "yo", "ok", "okay", "yes", "no", "maybe"}
    if normalized in greetings:
        return True

    return len(normalized.split()) <= 2 and any(word in greetings for word in normalized.split())


def _is_long_complex(message_text: str) -> bool:
    text = message_text.strip()
    if len(text) > 160:
        return True

    words = text.split()
    if len(words) >= 20:
        return True

    return any(p in text for p in ["?", ".", ","]) and len(words) >= 12


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

    if detect_scheduling_conflict(intent_result):
        return HandoffDecision(
            should_handoff=True,
            reason="scheduling_conflict",
            suggested_action="Review availability and propose a concrete slot over phone or email.",
            summary=_format_summary(fsm_reply.summary),
        )

    can_fsm_continue = fsm_reply.progress is not None or bool(fsm_reply.quick_replies)
    is_routing_step = fsm_reply.step in {None, FsmStep.routing}

    if intent_result.intent == Intent.faq and faq_matches is not None and not faq_matches:
        reason = "faq_unclear"
        if intent_result.confidence < HANDOFF_CONFIDENCE_THRESHOLD or not any(ch.isalpha() for ch in message_text):
            reason = "low_confidence"

        return HandoffDecision(
            should_handoff=True,
            reason=reason,
            suggested_action="Provide a direct answer and share a link to relevant help content.",
            summary=_format_summary(fsm_reply.summary),
        )

    if intent_result.confidence < HANDOFF_CONFIDENCE_THRESHOLD and not faq_matches:
        if (
            not can_fsm_continue
            and is_routing_step
            and not _is_short_or_greeting(message_text)
            and (_is_long_complex(message_text) or intent_result.confidence < LOW_CONFIDENCE_DEEP_THRESHOLD)
        ):
            return HandoffDecision(
                should_handoff=True,
                reason="low_confidence",
                suggested_action="Review intent, reply manually, and confirm details.",
                summary=_format_summary(fsm_reply.summary),
            )
        return HandoffDecision(should_handoff=False)

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
