from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator


class ReasonKind(StrEnum):
    TIME_OVERRUN = "TIME_OVERRUN"
    PRICE_ADJUST = "PRICE_ADJUST"


class ReasonCode(StrEnum):
    ACCESS_DELAY = "ACCESS_DELAY"
    EXTRA_DIRT = "EXTRA_DIRT"
    CLIENT_REQUEST = "CLIENT_REQUEST"
    SUPPLIES_MISSING = "SUPPLIES_MISSING"
    ESTIMATE_WRONG = "ESTIMATE_WRONG"
    PARKING_DELAY = "PARKING_DELAY"
    ADDON_ADDED = "ADDON_ADDED"
    DAMAGE_RISK = "DAMAGE_RISK"
    DISCOUNT_PROMO = "DISCOUNT_PROMO"
    CLIENT_COMPLAINT = "CLIENT_COMPLAINT"
    EXTRA_SERVICE = "EXTRA_SERVICE"
    OTHER = "OTHER"


TIME_OVERRUN_CODES = {
    ReasonCode.ACCESS_DELAY,
    ReasonCode.EXTRA_DIRT,
    ReasonCode.CLIENT_REQUEST,
    ReasonCode.SUPPLIES_MISSING,
    ReasonCode.ESTIMATE_WRONG,
    ReasonCode.PARKING_DELAY,
    ReasonCode.OTHER,
}

PRICE_ADJUST_CODES = {
    ReasonCode.ADDON_ADDED,
    ReasonCode.DAMAGE_RISK,
    ReasonCode.DISCOUNT_PROMO,
    ReasonCode.CLIENT_COMPLAINT,
    ReasonCode.EXTRA_SERVICE,
    ReasonCode.OTHER,
}


class ReasonCreateRequest(BaseModel):
    kind: ReasonKind
    code: ReasonCode
    note: str | None = Field(default=None, max_length=1000)
    time_entry_id: str | None = None
    invoice_item_id: int | None = None

    @model_validator(mode="after")
    def validate_code_for_kind(self) -> "ReasonCreateRequest":
        if self.kind == ReasonKind.TIME_OVERRUN and self.code not in TIME_OVERRUN_CODES:
            raise ValueError("Invalid code for TIME_OVERRUN")
        if self.kind == ReasonKind.PRICE_ADJUST and self.code not in PRICE_ADJUST_CODES:
            raise ValueError("Invalid code for PRICE_ADJUST")
        return self

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class ReasonResponse(BaseModel):
    reason_id: str
    order_id: str
    kind: ReasonKind
    code: ReasonCode
    note: str | None = None
    created_at: datetime
    created_by: str | None = None
    time_entry_id: str | None = None
    invoice_item_id: int | None = None

    @staticmethod
    def from_model(model) -> "ReasonResponse":
        return ReasonResponse(
            reason_id=model.reason_id,
            order_id=model.order_id,
            kind=ReasonKind(model.kind),
            code=ReasonCode(model.code),
            note=model.note,
            created_at=model.created_at,
            created_by=model.created_by,
            time_entry_id=model.time_entry_id,
            invoice_item_id=model.invoice_item_id,
        )


class ReasonListResponse(BaseModel):
    reasons: list[ReasonResponse]
