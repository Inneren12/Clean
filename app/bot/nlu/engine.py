from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Tuple

from app.bot.nlu.models import Entities, Intent, IntentResult, TimeWindow
from app.bot.rules.config import UPSOLD_EXTRAS

logger = logging.getLogger(__name__)


INTENT_KEYWORDS: Dict[Intent, List[str]] = {
    Intent.booking: [
        "book",
        "schedule",
        "reserve",
        "appointment",
        "slot",
        "cleaning",
        "clean my",
        "need cleaning",
        "забронировать",
        "записать",
        "уборку",
        "уборка",
    ],
    Intent.price: [
        "price",
        "quote",
        "cost",
        "how much",
        "estimate",
        "сколько",
        "цена",
        "стоимость",
    ],
    Intent.scope: [
        "include",
        "scope",
        "what's included",
        "services",
        "coverage",
        "что входит",
        "инструменты",
        "can you",
        "do you",
    ],
    Intent.reschedule: [
        "reschedule",
        "move",
        "change time",
        "change the time",
        "another time",
        "перенести",
        "сменить время",
    ],
    Intent.cancel: [
        "cancel",
        "call off",
        "stop",
        "отменить",
        "не нужно",
    ],
    Intent.status: [
        "status",
        "update",
        "where is",
        "eta",
        "progress",
        "статус",
        "когда будет",
    ],
    Intent.faq: ["faq", "info", "information", "tell me"],
    Intent.human: ["human", "agent", "representative", "someone", "оператор"],
    Intent.complaint: [
        "complaint",
        "angry",
        "bad",
        "upset",
        "issue",
        "problem",
        "not happy",
        "refund",
        "жалоба",
        "проблема",
    ],
}

CANCEL_LEXEMES = ["cancel", "call off", "отменить"]


INTENT_PATTERNS: Dict[Intent, List[re.Pattern[str]]] = {
    Intent.booking: [re.compile(r"\b(book|schedule) (a|the)? ?(clean|service)")],
    Intent.price: [re.compile(r"\bhow (much|many)\b"), re.compile(r"\bprice (quote)?\b")],
    Intent.reschedule: [
        re.compile(r"\b(reschedule|move) (my|the) (booking|appointment)"),
        re.compile(r"\bmove (my )?slot\b"),
        re.compile(r"change (the )?time"),
    ],
    Intent.cancel: [re.compile(r"\b(cancel|call off) (my|the) (booking|appointment)")],
    Intent.human: [re.compile(r"\b(human|agent|representative)\b")],
    Intent.complaint: [re.compile(r"\b(complaint|refund)\b")],
    Intent.status: [re.compile(r"\b(status|update|eta)\b")],
    Intent.scope: [
        re.compile(r"\b(can|do) you clean\b"),
        re.compile(r"\bwhat (do you|will you) clean\b"),
    ],
}

DAY_WORDS = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
    "today",
    "tomorrow",
]

TIME_LABELS = {
    "morning": TimeWindow(start="08:00", end="12:00", label="morning"),
    "afternoon": TimeWindow(start="12:00", end="17:00", label="afternoon"),
    "evening": TimeWindow(start="17:00", end="21:00", label="evening"),
    "today": TimeWindow(label="today", day="today"),
    "tomorrow": TimeWindow(label="tomorrow", day="tomorrow"),
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _score_intent(normalized: str) -> Tuple[Intent, float, List[str]]:
    scores: Dict[Intent, float] = {intent: 0.0 for intent in Intent}
    reasons: Dict[Intent, List[str]] = {intent: [] for intent in Intent}

    for intent, keywords in INTENT_KEYWORDS.items():
        for keyword in keywords:
            if keyword in normalized:
                scores[intent] += 0.15
                reasons[intent].append(f"keyword match: '{keyword}'")

    for intent, patterns in INTENT_PATTERNS.items():
        for pattern in patterns:
            if pattern.search(normalized):
                scores[intent] += 0.25
                reasons[intent].append(f"pattern match: {pattern.pattern}")

    best_intent = max(scores, key=scores.get)
    best_score = scores[best_intent]

    price_match = scores[Intent.price] == best_score and scores[Intent.price] > 0
    if price_match:
        best_intent = Intent.price
        best_score = scores[Intent.price]

    reschedule_signal = (
        ("move" in normalized and "move out" not in normalized)
        or "reschedule" in normalized
        or "change" in normalized
    )
    if best_intent == Intent.booking and scores[Intent.reschedule] >= scores[Intent.booking] and reschedule_signal:
        best_intent = Intent.reschedule
        best_score = scores[Intent.reschedule]

    if best_score < 0.15:
        best_intent = Intent.faq
        best_score = 0.25
        reasons[best_intent].append("fallback to faq")

    confidence = min(1.0, round(best_score + 0.35 if best_score >= 0.45 else best_score + 0.1, 2))

    return best_intent, confidence, reasons[best_intent]


def _parse_int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0


def _extract_beds_baths(normalized: str, entities: Entities, reasons: List[str]) -> None:
    beds_match = re.search(r"(\d+)\s*(bed|beds|br|bdrm)\b", normalized)
    baths_match = re.search(r"(\d+)\s*(bath|baths|ba|bathroom)\b", normalized)
    combo_match = re.search(r"(\d+)\s*[xх]\s*(\d+)", normalized)

    if combo_match:
        beds_val = _parse_int(combo_match.group(1))
        baths_val = _parse_int(combo_match.group(2))
        if beds_val:
            entities.beds = beds_val
            reasons.append(f"beds from combo: {beds_val}")
        if baths_val:
            entities.baths = baths_val
            reasons.append(f"baths from combo: {baths_val}")

    if beds_match:
        entities.beds = _parse_int(beds_match.group(1))
        reasons.append(f"beds: {entities.beds}")
    if baths_match:
        entities.baths = _parse_int(baths_match.group(1))
        reasons.append(f"baths: {entities.baths}")


def _extract_size(normalized: str, entities: Entities, reasons: List[str]) -> None:
    sqft_match = re.search(r"(\d{3,5})\s*(sq\s?ft|ft2|ft²)", normalized)
    m2_match = re.search(r"(\d{2,5})\s*(m2|sq\s?m|м2)", normalized)
    if sqft_match:
        entities.square_feet = _parse_int(sqft_match.group(1))
        reasons.append(f"square feet: {entities.square_feet}")
    if m2_match:
        entities.square_meters = _parse_int(m2_match.group(1))
        reasons.append(f"square meters: {entities.square_meters}")


SERVICE_KEYWORDS = {
    "regular": ["regular", "standard", "basic", "обычная", "стандарт"],
    "deep_clean": ["deep", "detailed", "spring", "генеральная", "глубокая"],
    "move_out": ["move-out", "move out", "moving", "выезд", "переезд"],
    "post_renovation": ["post-renovation", "renovation", "construction", "после ремонта", "ремонт"],
}


EXTRA_KEYWORDS = {name: rule.keywords for name, rule in UPSOLD_EXTRAS.items()}


def _has_keyword(normalized: str, keyword: str) -> bool:
    if keyword.replace(" ", "").replace("-", "").isalnum():
        return bool(re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", normalized))
    return keyword in normalized

PROPERTY_TYPES = {
    "apartment": ["apartment", "apt", "квартира"],
    "house": ["house", "home", "дом"],
    "office": ["office", "офис"],
    "studio": ["studio"],
}

CONDITION_KEYWORDS = {
    "light": ["light", "easy", "quick", "slight"],
    "standard": ["normal", "standard", "обычная"],
    "heavy": ["messy", "dirty", "heavy", "deep dirt", "гряз"],
}

SIZE_HINTS = {
    "studio": ["studio", "студия"],
    "loft": ["loft", "лофт"],
}


def _extract_service(normalized: str, entities: Entities, reasons: List[str]) -> None:
    for service, keywords in SERVICE_KEYWORDS.items():
        if any(keyword in normalized for keyword in keywords):
            entities.service_type = service
            reasons.append(f"service type: {service}")
            break

    extras: List[str] = []
    for extra, keywords in EXTRA_KEYWORDS.items():
        if any(_has_keyword(normalized, keyword) for keyword in keywords):
            extras.append(extra)
    if extras:
        entities.extras = sorted(set(extras))
        reasons.append(f"extras: {', '.join(entities.extras)}")


def _extract_property_type(normalized: str, entities: Entities, reasons: List[str]) -> None:
    for property_type, keywords in PROPERTY_TYPES.items():
        if any(re.search(rf"\b{re.escape(keyword)}\b", normalized) for keyword in keywords):
            entities.property_type = property_type
            reasons.append(f"property type: {property_type}")
            break


def _extract_condition(normalized: str, entities: Entities, reasons: List[str]) -> None:
    for condition, keywords in CONDITION_KEYWORDS.items():
        if any(keyword in normalized for keyword in keywords):
            entities.condition = condition
            reasons.append(f"condition: {condition}")
            break


def _extract_size_label(normalized: str, entities: Entities, reasons: List[str]) -> None:
    for label, keywords in SIZE_HINTS.items():
        if any(keyword in normalized for keyword in keywords):
            entities.size_label = label
            reasons.append(f"size label: {label}")
            break


TIME_PATTERN = re.compile(r"(after|by|before)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?")


def _normalize_time(hour: int, minute: int, meridiem: str | None) -> str:
    if meridiem:
        meridiem = meridiem.lower()
        if meridiem == "pm" and hour < 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
    return f"{hour:02d}:{minute:02d}"


def _extract_day(normalized: str) -> Optional[str]:
    return next((day for day in DAY_WORDS if day in normalized), None)


def _extract_time_window(normalized: str, entities: Entities, reasons: List[str]) -> None:
    day_match = _extract_day(normalized)

    label_match = next((key for key in TIME_LABELS if key in normalized), None)
    time_match = TIME_PATTERN.search(normalized)

    qualifier_window: TimeWindow | None = None
    label_window: TimeWindow | None = None

    if time_match:
        qualifier, hour_str, minute_str, meridiem = time_match.groups()
        start_time = _normalize_time(int(hour_str), int(minute_str or 0), meridiem)
        qualifier_window = TimeWindow(start=start_time, label=qualifier)
        if qualifier == "before":
            qualifier_window.end = start_time
            qualifier_window.start = None
        elif qualifier == "by":
            qualifier_window.end = start_time
        if day_match:
            qualifier_window.day = day_match
            reasons.append(f"day detected: {day_match}")
        reasons.append(f"time qualifier: {qualifier} {start_time}")

    if label_match:
        label_window = TimeWindow(
            start=TIME_LABELS[label_match].start,
            end=TIME_LABELS[label_match].end,
            label=TIME_LABELS[label_match].label,
            day=TIME_LABELS[label_match].day or day_match,
        )
        reasons.append(f"time window label: {label_match}")
        if day_match and not label_window.day:
            reasons.append(f"day detected: {day_match}")

    if qualifier_window and label_window:
        entities.time_window = TimeWindow(
            start=qualifier_window.start or label_window.start,
            end=qualifier_window.end or label_window.end,
            label=label_window.label or qualifier_window.label,
            day=qualifier_window.day or label_window.day or day_match,
        )
    elif qualifier_window:
        entities.time_window = qualifier_window
    elif label_window:
        entities.time_window = label_window
    elif day_match:
        entities.time_window = TimeWindow(label=day_match, day=day_match)
        reasons.append(f"day detected: {day_match}")


AREA_PATTERN = re.compile(r"(?:in|around|near|location|area)\s+([a-zA-Z\s]{3,40})", re.IGNORECASE)
AREA_STOPWORDS = {
    "book",
    "need",
    "schedule",
    "quote",
    "please",
    "tell",
    "can",
    "do",
    "move",
}
LOCATION_HINTS = {"in", "near", "around", "at", "to", "by"}


def _extract_area(original: str, normalized: str, entities: Entities, reasons: List[str]) -> None:
    match = AREA_PATTERN.search(original)
    if match:
        area = match.group(1).strip().rstrip(".?!")
        first_token = area.split()[0] if area else ""
        if first_token.lower() in AREA_STOPWORDS:
            return
        entities.area = re.sub(r"\s+", " ", area)
        reasons.append(f"area: {entities.area}")
        return

    tokens = original.split()
    for index, token in enumerate(tokens):
        if not token.istitle() or len(token) <= 3:
            continue
        if token.lower() in AREA_STOPWORDS:
            continue
        previous = tokens[index - 1].lower() if index > 0 else ""
        if previous in LOCATION_HINTS or index == len(tokens) - 1:
            entities.area = token
            reasons.append(f"area guess: {entities.area}")
            break


def extract_entities(text: str) -> Tuple[Entities, List[str]]:
    normalized = _normalize(text)
    entities = Entities()
    reasons: List[str] = []

    _extract_beds_baths(normalized, entities, reasons)
    _extract_size(normalized, entities, reasons)
    _extract_service(normalized, entities, reasons)
    _extract_property_type(normalized, entities, reasons)
    _extract_condition(normalized, entities, reasons)
    _extract_size_label(normalized, entities, reasons)
    _extract_time_window(normalized, entities, reasons)
    _extract_area(text, normalized, entities, reasons)

    return entities, reasons


def analyze_message(text: str) -> IntentResult:
    normalized = _normalize(text)
    intent, confidence, intent_reasons = _score_intent(normalized)
    entities, entity_reasons = extract_entities(text)

    if intent == Intent.faq and entities.service_type and (
        entities.beds
        or entities.baths
        or entities.square_feet
        or entities.square_meters
        or entities.size_label
        or entities.extras
    ):
        intent = Intent.booking
        confidence = max(confidence, 0.55)
        intent_reasons.append("fast-path booking: service and size detected")

    if intent == Intent.reschedule and entities.service_type == "move_out":
        intent = Intent.booking
        confidence = max(confidence, 0.5)
        intent_reasons.append("booking: move-out request")

    if any(lexeme in normalized for lexeme in CANCEL_LEXEMES):
        if intent != Intent.cancel:
            intent_reasons.append("override: cancel lexeme")
        intent = Intent.cancel

    combined_reasons = intent_reasons + entity_reasons
    if confidence < 0.4:
        combined_reasons.append("low confidence")

    logger.debug(
        "nlu_analysis",
        extra={
            "intent": intent.value,
            "confidence": confidence,
            "reasons": combined_reasons,
            "entities": entities.model_dump(exclude_none=True, by_alias=True),
        },
    )

    return IntentResult(intent=intent, confidence=confidence, reasons=combined_reasons, entities=entities)
