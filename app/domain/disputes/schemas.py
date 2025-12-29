from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DisputeState(str, Enum):
    OPEN = "OPEN"
    FACTS_COLLECTED = "FACTS_COLLECTED"
    DECIDED = "DECIDED"
    CLOSED = "CLOSED"


class DecisionType(str, Enum):
    NO_REFUND = "no_refund"
    PARTIAL_REFUND = "partial_refund"
    FULL_REFUND = "full_refund"
    CREDIT_NOTE = "credit_note"


class DisputeFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    photo_refs: list[str] = Field(default_factory=list)
    checklist_snapshot: dict[str, Any] | None = None
    time_log: dict[str, Any] | None = None
