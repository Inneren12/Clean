from __future__ import annotations

from collections import Counter
from typing import Optional

from app.bot.handoff.reasons import HandoffReason
from app.domain.bot.schemas import FsmStep


class InMemoryHandoffMetrics:
    def __init__(self) -> None:
        self.reasons: Counter[str] = Counter()
        self.drop_off_steps: Counter[str] = Counter()

    def record(self, reason: str | HandoffReason, step: Optional[FsmStep | str]) -> None:
        reason_code = HandoffReason.stable_reason(reason.value if isinstance(reason, HandoffReason) else str(reason))
        self.reasons[reason_code] += 1
        if step:
            self.drop_off_steps[str(step)] += 1

    def snapshot(self) -> dict:
        return {
            "reasons": dict(self.reasons),
            "drop_off_steps": dict(self.drop_off_steps),
        }


metrics = InMemoryHandoffMetrics()
