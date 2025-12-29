from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


POLICY_VERSION = "2024-09-01"


class DepositSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required: bool
    amount_cents: int | None = None
    percent_applied: float | None = None
    min_cents: int = 0
    max_cents: int = 0
    reasons: list[str] = Field(default_factory=list)
    basis: Literal["percent_clamped", "fixed_minimum", "none", "disabled"] = "none"


class CancellationWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Literal["free", "partial", "late"]
    start_hours_before: float | None
    end_hours_before: float | None
    refund_percent: int


class CancellationPolicySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[str] = Field(default_factory=list)
    windows: list[CancellationWindow]


class BookingPolicySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = POLICY_VERSION
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    lead_time_hours: float
    service_type: str | None = None
    total_amount_cents: int | None = None
    first_time_client: bool = False
    deposit: DepositSnapshot
    cancellation: CancellationPolicySnapshot
