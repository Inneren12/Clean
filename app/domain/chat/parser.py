import re
from typing import List, Tuple

from app.domain.chat.models import ParsedFields
from app.domain.pricing.models import AddOns, CleaningType, Frequency

BED_RE = re.compile(r"(?P<count>\d+)\s*(?:bed|bedroom|br)\b", re.IGNORECASE)
BATH_RE = re.compile(r"(?P<count>\d+(?:\.5)?)\s*(?:bath|ba)\b", re.IGNORECASE)

CLEANING_TYPE_MAP = {
    "deep": CleaningType.deep,
    "standard": CleaningType.standard,
    "move out": CleaningType.move_out_empty,
    "move-out": CleaningType.move_out_empty,
    "move in": CleaningType.move_in_empty,
    "move-in": CleaningType.move_in_empty,
    "empty": CleaningType.move_out_empty,
}

FREQUENCY_MAP = {
    "weekly": Frequency.weekly,
    "biweekly": Frequency.biweekly,
    "bi-weekly": Frequency.biweekly,
    "one-time": Frequency.one_time,
    "one time": Frequency.one_time,
    "monthly": Frequency.monthly,
}

HEAVY_GREASE_KEYWORDS = ["grease", "greasy", "buildup", "hard water", "limescale"]
MULTI_FLOOR_KEYWORDS = ["stairs", "two-storey", "two story", "2 floors", "2-storey", "two floors"]

STEAM_ITEMS = {
    "armchair": "steam_armchair",
    "sofa 2": "steam_sofa_2",
    "sofa two": "steam_sofa_2",
    "2-seater": "steam_sofa_2",
    "sofa 3": "steam_sofa_3",
    "sofa three": "steam_sofa_3",
    "3-seater": "steam_sofa_3",
    "sectional": "steam_sectional",
    "mattress": "steam_mattress",
}


def _count_for(keyword: str, message: str) -> int:
    pattern = re.compile(rf"(?P<count>\d+)\s*{re.escape(keyword)}", re.IGNORECASE)
    match = pattern.search(message)
    if match:
        return int(match.group("count"))
    return 1


def parse_message(message: str) -> Tuple[ParsedFields, float, List[str]]:
    lowered = message.lower()
    add_ons = AddOns()

    beds_match = BED_RE.search(lowered)
    baths_match = BATH_RE.search(lowered)

    beds = int(beds_match.group("count")) if beds_match else None
    baths = float(baths_match.group("count")) if baths_match else None

    cleaning_type = None
    for key, value in CLEANING_TYPE_MAP.items():
        if key in lowered:
            cleaning_type = value
            break

    frequency = None
    for key, value in FREQUENCY_MAP.items():
        if key in lowered:
            frequency = value
            break

    if any(keyword in lowered for keyword in HEAVY_GREASE_KEYWORDS):
        heavy_grease = True
    else:
        heavy_grease = None

    if any(keyword in lowered for keyword in MULTI_FLOOR_KEYWORDS):
        multi_floor = True
    else:
        multi_floor = None

    if "oven" in lowered:
        add_ons.oven = True
    if "fridge" in lowered or "refrigerator" in lowered:
        add_ons.fridge = True
    if "microwave" in lowered:
        add_ons.microwave = True
    if "cabinets" in lowered:
        add_ons.cabinets = True
    if "windows" in lowered:
        add_ons.windows_up_to_5 = True
    if "balcony" in lowered:
        add_ons.balcony = True
    if "linen" in lowered or "linens" in lowered:
        if beds is not None:
            add_ons.linen_beds = beds
        else:
            add_ons.linen_beds = 1
    if "carpet spot" in lowered or "carpet stain" in lowered:
        add_ons.carpet_spot = _count_for("carpet", lowered)

    if "steam" in lowered:
        for keyword, field in STEAM_ITEMS.items():
            if keyword in lowered:
                setattr(add_ons, field, _count_for(keyword, lowered))
    if "chair" in lowered and "steam" in lowered:
        add_ons.steam_armchair = max(add_ons.steam_armchair, _count_for("chair", lowered))

    parsed = ParsedFields(
        beds=beds,
        baths=baths,
        cleaning_type=cleaning_type,
        heavy_grease=heavy_grease,
        multi_floor=multi_floor,
        frequency=frequency,
        add_ons=add_ons,
    )

    missing = []
    if beds is None:
        missing.append("beds")
    if baths is None:
        missing.append("baths")
    if cleaning_type is None:
        missing.append("cleaning_type")

    confidence = 1.0 - (len(missing) * 0.2)
    confidence = max(0.0, min(1.0, confidence))
    return parsed, confidence, missing
