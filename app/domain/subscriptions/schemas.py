from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

from app.domain.subscriptions import statuses


class SubscriptionCreateRequest(BaseModel):
    frequency: str
    start_date: date
    preferred_weekday: int | None = Field(default=None, ge=0, le=6)
    preferred_day_of_month: int | None = Field(default=None, ge=1, le=28)
    base_service_type: str = Field(min_length=1, max_length=100)
    base_price: int = Field(ge=0)

    @field_validator("frequency")
    @classmethod
    def normalize_frequency(cls, value: str) -> str:
        return statuses.normalize_frequency(value)


class SubscriptionUpdateRequest(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def normalize_status(cls, value: str) -> str:
        return statuses.normalize_status(value)


class SubscriptionResponse(BaseModel):
    subscription_id: str
    client_id: str
    status: str
    frequency: str
    start_date: date
    next_run_at: datetime
    preferred_weekday: int | None = None
    preferred_day_of_month: int | None = None
    base_service_type: str
    base_price: int
    created_at: datetime


class AdminSubscriptionListItem(BaseModel):
    subscription_id: str
    client_id: str
    status: str
    frequency: str
    next_run_at: datetime
    base_service_type: str
    base_price: int
    created_at: datetime


class SubscriptionRunResult(BaseModel):
    processed: int
    created_orders: int
