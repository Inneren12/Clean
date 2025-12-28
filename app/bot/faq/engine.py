from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class FaqEntry:
    question: str
    answer: str
    keywords: List[str]
    tags: List[str]


@dataclass
class FaqMatch:
    entry: FaqEntry
    score: int


FAQ_ENTRIES: List[FaqEntry] = [
    FaqEntry(
        question="How do you price cleaning?",
        answer=(
            "We price by cleaner-hours using your service type, property, size, and extras. "
            "Upfront ranges show minimum and maximum time on site."
        ),
        keywords=["price", "quote", "cost", "estimate", "pricing"],
        tags=["pricing", "estimate", "cost"],
    ),
    FaqEntry(
        question="What do you include in a standard clean?",
        answer=(
            "Standard cleanings cover floors, bathrooms, kitchen surfaces, dusting, and a tidy reset. "
            "Deep cleans add extra time for detail work."
        ),
        keywords=["include", "included", "scope", "services", "standard"],
        tags=["scope", "coverage", "included"],
    ),
    FaqEntry(
        question="Can I reschedule or talk to someone?",
        answer=(
            "Yes. Share your preferred day/time and contact info and a dispatcher will confirm. "
            "If you want a human now, say so and we will handoff."
        ),
        keywords=["reschedule", "change", "human", "agent", "person"],
        tags=["reschedule", "human", "handoff"],
    ),
]


def match_faq(text: str, limit: int = 3) -> List[FaqMatch]:
    normalized = text.lower()
    matches: List[FaqMatch] = []

    for entry in FAQ_ENTRIES:
        keyword_hits = sum(1 for keyword in entry.keywords if keyword in normalized)
        tag_hits = sum(1 for tag in entry.tags if tag in normalized)
        score = keyword_hits + tag_hits
        if score > 0:
            matches.append(FaqMatch(entry=entry, score=score))

    matches.sort(key=lambda match: match.score, reverse=True)
    top_matches = matches[:limit]

    if not top_matches:
        return []

    best_score = top_matches[0].score
    minimum_score = max(1, best_score - 1)
    return [match for match in top_matches if match.score >= minimum_score]


def format_matches(matches: List[FaqMatch]) -> str:
    lines = ["Here’s what I found:"]
    for index, match in enumerate(matches, start=1):
        lines.append(f"{index}. {match.entry.question}")
        lines.append(f"   {match.entry.answer}")
    return "\n".join(lines)
