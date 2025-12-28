from __future__ import annotations

from collections import Counter
from typing import Optional

from app.domain.bot.schemas import FsmStep


class InMemoryHandoffMetrics:
    def __init__(self) -> None:
        self.reasons: Counter[str] = Counter()
        self.drop_off_steps: Counter[str] = Counter()

    def record(self, reason: str, step: Optional[FsmStep | str]) -> None:
        self.reasons[reason] += 1
        if step:
            self.drop_off_steps[str(step)] += 1

    def snapshot(self) -> dict:
        return {
            "reasons": dict(self.reasons),
            "drop_off_steps": dict(self.drop_off_steps),
        }


metrics = InMemoryHandoffMetrics()
