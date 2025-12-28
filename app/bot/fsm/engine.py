from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Dict, List, Optional

from app.bot.nlu.models import Entities, Intent, IntentResult
from app.bot.pricing.engine import PriceEstimate, PricingEngine, PricingInput
from app.domain.bot.schemas import ConversationState, FsmStep


@dataclass
class FsmReply:
    text: str
    quick_replies: List[str]
    progress: Optional[Dict[str, int]]
    summary: Dict[str, Any]
    step: Optional[FsmStep]
    estimate: Optional[PriceEstimate]

    @property
    def metadata(self) -> Dict[str, Any]:
        step_value = self.step.value if isinstance(self.step, Enum) else self.step
        return {
            "quickReplies": self.quick_replies,
            "progress": self.progress,
            "summary": self.summary,
            "fsmStep": step_value if self.step else None,
            "estimate": self.estimate.model_dump() if self.estimate else None,
        }


SERVICE_REPLIES = ["regular", "deep clean", "move-out", "post-renovation"]
PROPERTY_REPLIES = ["apartment", "house", "office", "studio"]
CONDITION_REPLIES = ["light", "standard", "heavy"]
SIZE_REPLIES = ["studio", "1 bed", "2 bed", "3+ bed", "1000 sqft"]
EXTRA_REPLIES = ["oven", "fridge", "windows", "carpet", "pets"]
TIME_REPLIES = ["morning", "afternoon", "evening", "specific time"]
CONTACT_REPLIES = ["share email", "share phone", "skip"]

FLOW_INTENTS = {Intent.booking, Intent.price, Intent.scope, Intent.reschedule}
HARD_INTERRUPTS = {Intent.human, Intent.complaint}
SOFT_INTERRUPTS = {Intent.status, Intent.faq}


STEP_SEQUENCE: list[FsmStep] = [
    FsmStep.ask_service_type,
    FsmStep.ask_property_type,
    FsmStep.ask_size,
    FsmStep.ask_condition,
    FsmStep.ask_extras,
    FsmStep.ask_area,
    FsmStep.ask_preferred_time,
    FsmStep.ask_contact,
    FsmStep.confirm_lead,
]


def _format_time_window(entities: Entities) -> Optional[str]:
    window = entities.time_window
    if not window:
        return None

    label = (window.label or "").strip()
    start = window.start or ""
    end = window.end or ""
    parts = [window.day] if window.day else []

    if label:
        parts.append(label)

    if start and end and label not in {"morning", "afternoon", "evening"}:
        parts.append(f"{start}-{end}")
    elif start and not end and not label:
        parts.append(start)
    elif end and not start and not label:
        parts.append(f"by {end}")
    elif start and label:
        parts.append(start)

    return " ".join([p for p in parts if p]) or None


def _should_skip_contact(intent: Intent) -> bool:
    return intent in {Intent.price, Intent.scope}


def _has_field(filled: Dict[str, Any], step: FsmStep) -> bool:
    match step:
        case FsmStep.ask_service_type:
            return bool(filled.get("service_type"))
        case FsmStep.ask_property_type:
            return bool(filled.get("property_type"))
        case FsmStep.ask_size:
            return "size" in filled or "square_feet" in filled or "beds" in filled
        case FsmStep.ask_condition:
            return bool(filled.get("condition"))
        case FsmStep.ask_extras:
            return "extras" in filled
        case FsmStep.ask_area:
            return bool(filled.get("area"))
        case FsmStep.ask_preferred_time:
            return bool(filled.get("preferred_time_window"))
        case FsmStep.ask_contact:
            return bool(filled.get("contact"))
        case FsmStep.confirm_lead:
            return bool(filled.get("confirmation_ready"))
        case _:
            return False


def _question_for_step(step: FsmStep, locale: str = "en") -> tuple[str, List[str]]:
    if step == FsmStep.ask_service_type:
        return ("What type of cleaning do you need?", SERVICE_REPLIES)
    if step == FsmStep.ask_property_type:
        return ("What property is it?", PROPERTY_REPLIES)
    if step == FsmStep.ask_size:
        return ("How big is the place? beds/baths or sqft are great.", SIZE_REPLIES)
    if step == FsmStep.ask_condition:
        return ("What's the condition?", CONDITION_REPLIES)
    if step == FsmStep.ask_extras:
        return ("Any extras to include?", EXTRA_REPLIES)
    if step == FsmStep.ask_area:
        return ("Which area or neighborhood?", [])
    if step == FsmStep.ask_preferred_time:
        return ("Any preferred day/time?", TIME_REPLIES)
    if step == FsmStep.ask_contact:
        return ("How should we contact you?", CONTACT_REPLIES)
    if step == FsmStep.confirm_lead:
        return ("Shall I lock this in and confirm the booking?", ["Yes, confirm", "Edit details"])
    return ("Got it.", [])


def _update_fields(filled: Dict[str, Any], message_text: str, entities: Entities) -> Dict[str, Any]:
    updated = {**filled}
    updated["last_message"] = message_text
    normalized = message_text.lower()

    if entities.service_type:
        updated["service_type"] = entities.service_type
    if entities.property_type:
        updated["property_type"] = entities.property_type
    if entities.condition:
        updated["condition"] = entities.condition
    if entities.size_label:
        updated.setdefault("size", entities.size_label)
    if entities.beds:
        updated["beds"] = entities.beds
    if entities.baths:
        updated["baths"] = entities.baths
    if entities.square_feet:
        updated["square_feet"] = entities.square_feet
    if entities.square_meters:
        updated["square_meters"] = entities.square_meters
    if entities.extras:
        updated["extras"] = sorted(set(entities.extras))
    if entities.area:
        updated["area"] = entities.area

    if "no extras" in normalized or "без доп" in normalized:
        updated["extras"] = []

    email_match = re.search(r"[\w\.-]+@[\w\.-]+", message_text)
    if email_match:
        updated["contact"] = {**updated.get("contact", {}), "email": email_match.group(0)}

    phone_match = re.search(r"\+?\d[\d\s\-]{7,}\d", message_text)
    if phone_match:
        digits_only = re.sub(r"\s|-", "", phone_match.group(0))
        updated["contact"] = {**updated.get("contact", {}), "phone": digits_only}

    if normalized.strip() in {"skip", "no contact"}:
        updated["contact"] = {"provided": False}

    preferred_time = _format_time_window(entities)
    if preferred_time:
        updated["preferred_time_window"] = preferred_time

    entity_snapshot = entities.model_dump(exclude_none=True, by_alias=True)
    if entity_snapshot:
        updated["entities"] = entity_snapshot
    return updated


def _progress(sequence: List[FsmStep], filled: Dict[str, Any]) -> Dict[str, int]:
    missing = [step for step in sequence if not _has_field(filled, step)]
    total = len(sequence)
    completed = total - len(missing)
    current = completed + 1 if missing else total
    return {"current": current, "total": total}


def _summary(filled: Dict[str, Any]) -> Dict[str, Any]:
    keys = [
        "service_type",
        "property_type",
        "size",
        "beds",
        "baths",
        "condition",
        "extras",
        "area",
        "preferred_time_window",
        "contact",
    ]
    return {key: filled[key] for key in keys if key in filled}


class BotFsm:
    def __init__(self, state: Optional[ConversationState] = None, locale: str = "en") -> None:
        self.state = state or ConversationState()
        self.locale = locale
        self.pricing_engine = PricingEngine()

    def _steps_for_intent(self, intent: Intent) -> List[FsmStep]:
        steps = list(STEP_SEQUENCE)
        if _should_skip_contact(intent):
            steps = [step for step in steps if step not in {FsmStep.ask_contact, FsmStep.confirm_lead}]
        return steps

    def _estimate(self, filled: Dict[str, Any]) -> Optional[PriceEstimate]:
        if not filled.get("service_type"):
            return None
        pricing_input = PricingInput(
            service_type=str(filled.get("service_type")),
            property_type=filled.get("property_type"),
            size=str(filled.get("size")) if filled.get("size") else None,
            beds=filled.get("beds"),
            baths=filled.get("baths"),
            square_feet=filled.get("square_feet"),
            condition=filled.get("condition"),
            extras=filled.get("extras", []),
            area=filled.get("area"),
        )
        return self.pricing_engine.estimate(pricing_input)

    def handle(self, message_text: str, intent_result: IntentResult) -> FsmReply:
        filled = _update_fields(self.state.filled_fields, message_text, intent_result.entities)

        incoming_intent = intent_result.intent
        ongoing = self.state.current_intent in FLOW_INTENTS
        has_entities = bool(intent_result.entities.model_dump(exclude_none=True, by_alias=True))

        if incoming_intent in HARD_INTERRUPTS:
            step = FsmStep.handoff_check
            self.state = ConversationState(
                current_intent=incoming_intent,
                fsm_step=step,
                filled_fields=filled,
                confidence=intent_result.confidence,
                last_estimate=self.state.last_estimate,
            )
            return FsmReply(
                text="I'll connect you to a human right away to help with this.",
                quick_replies=[],
                progress=None,
                summary=_summary(filled),
                step=step,
                estimate=None,
            )

        if incoming_intent == Intent.cancel:
            step = FsmStep.routing
            self.state = ConversationState(
                current_intent=Intent.cancel,
                fsm_step=step,
                filled_fields=filled,
                confidence=intent_result.confidence,
                last_estimate=self.state.last_estimate,
            )
            return FsmReply(
                text="Understood, I've canceled the request. If you'd like to start again, let me know.",
                quick_replies=[],
                progress=None,
                summary=_summary(filled),
                step=step,
                estimate=None,
            )

        if incoming_intent in SOFT_INTERRUPTS and not (ongoing and has_entities):
            active_intent = self.state.current_intent if ongoing else self.state.current_intent or incoming_intent
            current_step = self.state.fsm_step or FsmStep.routing
            steps = self._steps_for_intent(active_intent) if active_intent in FLOW_INTENTS else []
            progress = _progress(steps, filled) if steps else None
            _, quick_replies = _question_for_step(current_step) if current_step else ("", [])
            self.state = ConversationState(
                current_intent=active_intent,
                fsm_step=current_step,
                filled_fields=filled,
                confidence=intent_result.confidence,
                last_estimate=self.state.last_estimate,
            )
            status_text = (
                "We're still on your request. I'll keep the flow ready for when you're ready to continue."
                if incoming_intent == Intent.status
                else "Here's some info. We can continue your request whenever you're ready."
            )
            return FsmReply(
                text=status_text,
                quick_replies=quick_replies,
                progress=progress,
                summary=_summary(filled),
                step=current_step,
                estimate=None,
            )

        active_intent = self.state.current_intent if ongoing else incoming_intent
        steps = self._steps_for_intent(active_intent)
        missing_steps = [step for step in steps if not _has_field(filled, step)]
        active_step = missing_steps[0] if missing_steps else (steps[-1] if steps else None)
        question, quick_replies = _question_for_step(active_step) if active_step else ("", [])

        estimate = self._estimate(filled)

        last_estimate = estimate.model_dump() if estimate else self.state.last_estimate

        text_parts: List[str] = []
        if estimate:
            text_parts.append(
                f"Estimate: ${estimate.price_range_min}-${estimate.price_range_max} • ~{estimate.duration_minutes} min."
            )
            if estimate.explanation:
                text_parts.append("; ".join(estimate.explanation[:3]))

        if active_step == FsmStep.confirm_lead and active_intent not in {Intent.price, Intent.scope}:
            filled["confirmation_ready"] = True
            text_parts.append("All details captured. Ready to confirm the booking.")
        elif question:
            text_parts.append(question)

        self.state = ConversationState(
            current_intent=active_intent,
            fsm_step=active_step,
            filled_fields=filled,
            confidence=intent_result.confidence,
            last_estimate=last_estimate,
        )

        return FsmReply(
            text="\n".join(text_parts).strip(),
            quick_replies=quick_replies,
            progress=_progress(steps, filled) if steps else None,
            summary=_summary(filled),
            step=active_step,
            estimate=estimate,
        )
