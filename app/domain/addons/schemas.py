from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class AddonDefinitionCreate(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=255)
    price_cents: int = Field(ge=0)
    default_minutes: int = Field(ge=0)
    is_active: bool = True


class AddonDefinitionUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    price_cents: int | None = Field(default=None, ge=0)
    default_minutes: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


class AddonDefinitionResponse(BaseModel):
    addon_id: int
    code: str
    name: str
    price_cents: int
    default_minutes: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class OrderAddonSelection(BaseModel):
    addon_id: int
    qty: int = Field(gt=0)

    @field_validator("qty")
    @classmethod
    def validate_qty(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Quantity must be positive")
        return value


class OrderAddonUpdateRequest(BaseModel):
    addons: list[OrderAddonSelection]


class OrderAddonResponse(BaseModel):
    order_addon_id: int
    order_id: str
    addon_id: int
    code: str
    name: str
    qty: int
    unit_price_cents: int
    minutes: int
    created_at: datetime


class AddonReportItem(BaseModel):
    addon_id: int
    code: str
    name: str
    total_qty: int
    revenue_cents: int


class AddonReportResponse(BaseModel):
    addons: list[AddonReportItem]
