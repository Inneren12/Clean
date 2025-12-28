from __future__ import annotations

from dataclasses import dataclass
from typing import List

FAQ_ENTRIES = [
    {
        "id": "pricing",
        "question": "How is pricing calculated?",
        "answer": "Pricing depends on service type, size, and condition. We provide a range and refine it with your details.",
        "keywords": ["price", "cost", "pricing", "quote", "estimate"],
        "tags": ["pricing", "quote"],
    },
    {
        "id": "included",
        "question": "What's included in a standard clean?",
        "answer": "Standard cleans cover kitchen, bathrooms, common areas, and routine dusting. Extras like oven/fridge/windows can be added.",
        "keywords": ["included", "include", "scope", "services", "standard", "regular"],
        "tags": ["scope", "coverage"],
    },
    {
        "id": "booking",
        "question": "How do I book a cleaning?",
        "answer": "Share the property type, size, and preferred time. We'll confirm availability and lock the slot with your contact details.",
        "keywords": ["book", "booking", "schedule", "appointment"],
        "tags": ["booking", "schedule"],
    },
    {
        "id": "supplies",
        "question": "Do you bring supplies?",
        "answer": "Yes, teams bring supplies for standard and deep cleans. Tell us if you have special surfaces so we can prepare.",
        "keywords": ["supplies", "bring", "equipment", "materials"],
        "tags": ["supplies"],
    },
]


@dataclass
class FaqMatch:
    id: str
    question: str
    answer: str
    score: int


def match_faq(message_text: str, *, limit: int = 3) -> List[FaqMatch]:
    normalized = message_text.lower()
    scored: list[FaqMatch] = []
    for entry in FAQ_ENTRIES:
        score = 0
        for keyword in entry.get("keywords", []):
            if keyword in normalized:
                score += 2
        for tag in entry.get("tags", []):
            if tag in normalized:
                score += 1
        if score > 0:
            scored.append(
                FaqMatch(
                    id=entry["id"],
                    question=entry["question"],
                    answer=entry["answer"],
                    score=score,
                )
            )
    scored.sort(key=lambda item: item.score, reverse=True)
    return scored[:limit]
