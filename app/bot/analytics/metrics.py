from __future__ import annotations

from collections import Counter
from typing import Dict, Optional


class BotMetrics:
    def __init__(self) -> None:
        self.handoff_reasons: Counter[str] = Counter()
        self.drop_off_steps: Counter[str] = Counter()

    def record_handoff(self, reason: str, step: Optional[str] = None) -> None:
        self.handoff_reasons[reason] += 1
        if step:
            self.drop_off_steps[step] += 1

    def snapshot(self) -> Dict[str, Dict[str, int]]:
        return {
            "handoff_reasons": dict(self.handoff_reasons),
            "drop_off_steps": dict(self.drop_off_steps),
        }
