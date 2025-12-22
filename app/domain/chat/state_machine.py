from typing import Dict, List, Optional, Tuple

from app.domain.chat.models import ChatTurnResponse, ChatTurnRequest, ParsedFields, Intent
from app.domain.chat.intents import detect_intent
from app.domain.chat.parser import parse_message
from app.domain.chat.responder import build_reply
from app.domain.pricing.estimator import estimate
from app.domain.pricing.models import CleaningType, EstimateRequest, Frequency
from app.domain.pricing.config_loader import PricingConfig

RED_FLAG_KEYWORDS = [
    "mold",
    "renovation",
    "construction dust",
    "hoarding",
    "biohazard",
    "feces",
    "needles",
]

REQUIRED_FIELDS = ["beds", "baths"]


def _merge_fields(existing: ParsedFields, incoming: ParsedFields) -> ParsedFields:
    data = existing.model_dump()
    for field, value in incoming.model_dump().items():
        if field == "add_ons":
            add_ons = existing.add_ons.model_copy(deep=True)
            incoming_add_ons = incoming.add_ons
            for add_on_field, add_on_value in incoming_add_ons.model_dump().items():
                if isinstance(add_on_value, bool):
                    if add_on_value:
                        setattr(add_ons, add_on_field, True)
                else:
                    if add_on_value:
                        setattr(add_ons, add_on_field, add_on_value)
            data["add_ons"] = add_ons
        else:
            if value is not None:
                data[field] = value
    return ParsedFields(**data)


def _missing_fields(fields: ParsedFields) -> List[str]:
    missing = []
    for name in REQUIRED_FIELDS:
        if getattr(fields, name) in (None, ""):
            missing.append(name)
    return missing


def _to_estimate_request(fields: ParsedFields) -> EstimateRequest:
    return EstimateRequest(
        beds=fields.beds or 0,
        baths=fields.baths or 0,
        cleaning_type=fields.cleaning_type or CleaningType.standard,
        heavy_grease=fields.heavy_grease or False,
        multi_floor=fields.multi_floor or False,
        frequency=fields.frequency or Frequency.one_time,
        add_ons=fields.add_ons,
    )


def handle_turn(
    request: ChatTurnRequest,
    session_state: Optional[ParsedFields],
    pricing_config: PricingConfig,
) -> Tuple[ChatTurnResponse, ParsedFields]:
    intent = detect_intent(request.message)
    parsed, confidence, _ = parse_message(request.message)
    state = session_state or ParsedFields()
    merged = _merge_fields(state, parsed)
    if merged.cleaning_type is None:
        merged = merged.model_copy(update={"cleaning_type": CleaningType.standard})

    lowered = request.message.lower()
    if any(keyword in lowered for keyword in RED_FLAG_KEYWORDS):
        response = ChatTurnResponse(
            session_id=request.session_id,
            intent=intent,
            parsed_fields=merged,
            state=merged.model_dump(mode="json"),
            missing_fields=_missing_fields(merged),
            proposed_questions=["Could you share your contact details so our team can follow up?"],
            reply_text=(
                "Thanks for the details. This sounds like a special situation, so we'll have a specialist follow up. "
                "Please share your contact info and availability."
            ),
            handoff_required=True,
            estimate=None,
            confidence=confidence,
        )
        return response, merged

    missing = _missing_fields(merged)
    estimate_response = None
    if not missing:
        estimate_response = estimate(_to_estimate_request(merged), pricing_config)

    reply_text, proposed_questions = build_reply(merged, missing, estimate_response)

    response = ChatTurnResponse(
        session_id=request.session_id,
        intent=intent,
        parsed_fields=merged,
        state=merged.model_dump(mode="json"),
        missing_fields=missing,
        proposed_questions=proposed_questions,
        reply_text=reply_text,
        handoff_required=False,
        estimate=estimate_response,
        confidence=confidence,
    )
    return response, merged
