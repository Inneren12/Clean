from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class PricingInput:
    service_type: str
    property_type: Optional[str] = None
    size: Optional[str] = None
    beds: Optional[int] = None
    baths: Optional[int] = None
    square_feet: Optional[int] = None
    condition: Optional[str] = None
    extras: List[str] = None
    area: Optional[str] = None

    def __post_init__(self) -> None:
        if self.extras is None:
            self.extras = []


@dataclass
class PriceEstimate:
    price_range_min: int
    price_range_max: int
    duration_minutes: int
    explanation: List[str]

    def model_dump(self) -> dict:
        return {
            "priceRange": [self.price_range_min, self.price_range_max],
            "durationMinutes": self.duration_minutes,
            "explanation": self.explanation,
        }


BASE_PRICE = {
    "regular": 120,
    "deep_clean": 200,
    "move_out": 240,
    "post_renovation": 280,
}

BASE_DURATION = {
    "regular": 120,
    "deep_clean": 180,
    "move_out": 200,
    "post_renovation": 240,
}

SIZE_MULTIPLIER = {
    "studio": 0.85,
    "small": 0.9,
    "medium": 1.0,
    "large": 1.25,
    "xl": 1.4,
}

CONDITION_MULTIPLIER = {
    "light": 0.9,
    "standard": 1.0,
    "heavy": 1.2,
}

EXTRA_PRICING = {
    "oven": 25,
    "fridge": 20,
    "windows": 40,
    "carpet": 35,
    "pets": 15,
}

ZONE_MULTIPLIER = {
    "manhattan": 1.15,
    "brooklyn": 1.1,
    "queens": 1.05,
}


def _size_band(input_: PricingInput) -> str:
    if input_.size:
        size_lower = input_.size.lower()
        if "studio" in size_lower:
            return "studio"
        if "3" in size_lower or "4" in size_lower:
            return "large"
        if "2" in size_lower:
            return "medium"
        if "1" in size_lower:
            return "small"

    if input_.square_feet:
        if input_.square_feet < 550:
            return "studio"
        if input_.square_feet < 850:
            return "small"
        if input_.square_feet < 1200:
            return "medium"
        if input_.square_feet < 1800:
            return "large"
        return "xl"

    if input_.beds:
        if input_.beds == 1:
            return "small"
        if input_.beds == 2:
            return "medium"
        if input_.beds >= 3:
            return "large"
    return "medium"


def _zone_multiplier(area: Optional[str]) -> float:
    if not area:
        return 1.0
    normalized = area.lower()
    for zone, multiplier in ZONE_MULTIPLIER.items():
        if zone in normalized:
            return multiplier
    return 1.0


class PricingEngine:
    def estimate(self, input_: PricingInput) -> PriceEstimate:
        service = input_.service_type or "regular"
        base_price = BASE_PRICE.get(service, BASE_PRICE["regular"])
        base_duration = BASE_DURATION.get(service, BASE_DURATION["regular"])

        size_band = _size_band(input_)
        size_multiplier = SIZE_MULTIPLIER.get(size_band, 1.0)
        condition_multiplier = CONDITION_MULTIPLIER.get(input_.condition or "standard", 1.0)
        extras_total = sum(EXTRA_PRICING.get(extra, 15) for extra in input_.extras)
        zone_multiplier = _zone_multiplier(input_.area)

        subtotal = (base_price + extras_total) * size_multiplier * condition_multiplier * zone_multiplier
        min_price = round(subtotal * 0.9)
        max_price = round(subtotal * 1.1)
        duration = int(base_duration * size_multiplier * condition_multiplier)

        explanation: List[str] = [
            f"Service base: ${base_price}",
            f"Size: {size_band} x{size_multiplier}",
            f"Condition x{condition_multiplier}",
        ]
        if extras_total:
            explanation.append(f"Extras: +${extras_total}")
        if zone_multiplier > 1:
            explanation.append(f"Area adjustment x{zone_multiplier}")

        return PriceEstimate(
            price_range_min=min_price,
            price_range_max=max_price,
            duration_minutes=duration,
            explanation=explanation,
        )
