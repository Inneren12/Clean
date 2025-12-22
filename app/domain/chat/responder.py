from typing import List, Optional, Tuple

from app.domain.chat.models import ParsedFields
from app.domain.pricing.models import EstimateResponse

QUESTION_MAP = {
    "beds": "How many bedrooms are in the home?",
    "baths": "How many bathrooms (e.g., 1, 1.5, 2)?",
    "cleaning_type": "Is this a standard, deep, or move-out clean?",
}


def build_reply(
    fields: ParsedFields,
    missing_fields: List[str],
    estimate: Optional[EstimateResponse],
) -> Tuple[str, List[str]]:
    if estimate:
        breakdown = estimate.breakdown
        reply_text = (
            "Great news! Here's your Economy estimate: "
            f"${breakdown.total_before_tax:.2f} before tax. "
            f"Labor: ${breakdown.labor_cost:.2f}, Add-ons: ${breakdown.add_ons_cost:.2f}, "
            f"Discounts: -${breakdown.discount_amount:.2f}. "
            f"Team size {breakdown.team_size}, time on site {breakdown.time_on_site_hours:.1f}h. "
            "Would you like to book a slot?"
        )
        return reply_text, []

    questions = [QUESTION_MAP[field] for field in missing_fields if field in QUESTION_MAP]
    questions = questions[:2]
    reply_text = "".join(
        [
            "Thanks! I can get you a quick estimate. ",
            " ".join(questions) if questions else "Tell me a bit more about the space.",
        ]
    )
    return reply_text, questions
