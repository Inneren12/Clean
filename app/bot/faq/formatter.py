from __future__ import annotations

from typing import List

from app.bot.faq.matcher import FaqMatch


DEFAULT_CLARIFICATION = (
    "I want to make sure I answer correctly. Which topic fits best?"
)
DEFAULT_QUICK_REPLIES = ["Pricing", "What's included", "Booking", "Human"]


def format_matches(matches: List[FaqMatch]) -> str:
    lines = ["Here's what I found:"]
    for match in matches[:3]:
        lines.append(f"- **{match.question}** — {match.answer}")
    return "\n".join(lines)


def clarification_prompt() -> tuple[str, List[str]]:
    return DEFAULT_CLARIFICATION, list(DEFAULT_QUICK_REPLIES)
