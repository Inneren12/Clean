from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from app.bot.fsm.engine import FsmReply
from app.bot.handoff.reasons import HandoffReason
from app.bot.nlu.models import Intent, IntentResult
from app.domain.bot.schemas import FsmStep


LOW_CONFIDENCE_THRESHOLD = 0.3
FLOW_INTENTS = {Intent.booking, Intent.price, Intent.scope, Intent.reschedule}
_GREETING_KEYWORDS = {"hi", "hello", "hey", "thanks", "thank you", "yo"}


@dataclass
class HandoffDecision:
    should_handoff: bool
    reason: Optional[HandoffReason] = None
    suggested_action: str = "continue"
    summary: str = ""


def _is_progressing(fsm_reply: FsmReply, current_intent: Optional[Intent] = None) -> bool:
    if current_intent and current_intent not in FLOW_INTENTS:
        return False
    if fsm_reply.progress is None or not fsm_reply.quick_replies:
        return False
    step_value = fsm_reply.step.value if isinstance(fsm_reply.step, FsmStep) else fsm_reply.step
    if not step_value:
        return False
    return str(step_value).startswith("ask_") or str(step_value) == FsmStep.confirm_lead.value


def _is_trivial_message(message_text: str) -> bool:
    normalized = message_text.strip().lower()
    word_count = len([w for w in normalized.split(" ") if w])
    return (
        len(normalized) <= 6
        or word_count <= 2
        or normalized in _GREETING_KEYWORDS
        or normalized in {"?", "??", "???"}
    )


def evaluate_handoff(
    intent_result: IntentResult,
    fsm_reply: FsmReply,
    message_text: str,
    faq_matches: Optional[List[dict]] = None,
    current_intent: Optional[Intent] = None,
) -> HandoffDecision:
    """Evaluate whether to hand off based on revised rules.

    This function is intentionally side-effect free.
    """

    intent = intent_result.intent
    normalized = message_text.strip().lower()
    progressing = _is_progressing(fsm_reply, current_intent)

    if intent in {Intent.complaint, Intent.human}:
        return HandoffDecision(
            should_handoff=True,
            reason=HandoffReason.complaint if intent == Intent.complaint else HandoffReason.human_requested,
            suggested_action="handoff",
            summary="User explicitly requested human support",
        )

    if intent == Intent.reschedule and intent_result.entities.time_window is None:
        return HandoffDecision(
            should_handoff=True,
            reason=HandoffReason.scheduling_conflict,
            suggested_action="handoff",
            summary="Reschedule requested without a viable time window",
        )

    if progressing:
        return HandoffDecision(
            should_handoff=False,
            reason=HandoffReason.fsm_progressing_suppress_faq,
            suggested_action="continue",
            summary="FSM flow is in progress; suppressing FAQ fallback handoff",
        )

    if faq_matches:
        return HandoffDecision(
            should_handoff=False,
            reason=HandoffReason.faq_matched,
            suggested_action="continue",
            summary="FAQ match found; no handoff needed",
        )

    low_confidence = intent_result.confidence < LOW_CONFIDENCE_THRESHOLD
    trivial_message = _is_trivial_message(message_text)

    if intent == Intent.faq and not (faq_matches or []):
        # FAQ unclear should ask for clarification, not handoff unless human keywords are present.
        if "human" in normalized or "agent" in normalized or "representative" in normalized:
            return HandoffDecision(
                should_handoff=True,
                reason=HandoffReason.human_requested,
                suggested_action="handoff",
                summary="User asked for human while requesting FAQ clarification",
            )
        if low_confidence and len(normalized.split()) >= 12:
            return HandoffDecision(
                should_handoff=True,
                reason=HandoffReason.faq_unclear_long,
                suggested_action="handoff",
                summary="Long FAQ message with very low confidence",
            )
        return HandoffDecision(
            should_handoff=False,
            reason=HandoffReason.faq_unclear,
            suggested_action="clarify",
            summary="FAQ unclear; suggest clarification",
        )

    if low_confidence:
        can_progress = bool(fsm_reply.quick_replies) and fsm_reply.progress is not None
        step_value = fsm_reply.step.value if isinstance(fsm_reply.step, FsmStep) else fsm_reply.step
        is_routing = str(step_value) == FsmStep.routing.value if step_value else False
        if not can_progress or is_routing:
            if trivial_message:
                return HandoffDecision(
                    should_handoff=False,
                    reason=HandoffReason.clarify_trivial,
                    suggested_action="clarify",
                    summary="Low-confidence short message; request clarification",
                )
            return HandoffDecision(
                should_handoff=True,
                reason=HandoffReason.low_confidence,
                suggested_action="handoff",
                summary="Low confidence and no active FSM path",
            )
        return HandoffDecision(
            should_handoff=False,
            reason=HandoffReason.low_confidence_continue,
            suggested_action="clarify",
            summary="Low confidence but FSM can continue",
        )

    return HandoffDecision(
        should_handoff=False,
        reason=HandoffReason.no_handoff,
        suggested_action="continue",
        summary="No handoff conditions met",
    )
