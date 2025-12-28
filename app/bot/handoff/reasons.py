from __future__ import annotations

from enum import Enum


class HandoffReason(str, Enum):
    complaint = "complaint"
    human_requested = "human_requested"
    scheduling_conflict = "scheduling_conflict"
    faq_unclear = "faq_unclear"
    faq_unclear_long = "faq_unclear_long"
    clarify_trivial = "clarify_trivial"
    low_confidence = "low_confidence"
    low_confidence_continue = "low_confidence_continue"
    faq_matched = "faq_matched"
    fsm_progressing_suppress_faq = "fsm_progressing_suppress_faq"
    no_handoff = "no_handoff"

    @classmethod
    def stable_reason(cls, value: str | None) -> str:
        try:
            return cls(value).value if value else cls.no_handoff.value
        except ValueError:
            return str(value or cls.no_handoff.value)
