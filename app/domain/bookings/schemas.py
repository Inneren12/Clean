from datetime import date, datetime, timezone

from pydantic import BaseModel, Field

from app.domain.bookings.service import LOCAL_TZ, round_duration_minutes


class SlotAvailabilityResponse(BaseModel):
    date: date
    duration_minutes: int
    slots: list[datetime]


class SlotQuery(BaseModel):
    date: date
    time_on_site_hours: float = Field(gt=0)
    postal_code: str | None = None

    @property
    def duration_minutes(self) -> int:
        return round_duration_minutes(self.time_on_site_hours)


class BookingCreateRequest(BaseModel):
    starts_at: datetime
    time_on_site_hours: float = Field(gt=0)
    lead_id: str | None = None

    @property
    def duration_minutes(self) -> int:
        return round_duration_minutes(self.time_on_site_hours)

    def normalized_start(self) -> datetime:
        local_start = self.starts_at
        if self.starts_at.tzinfo is None:
            local_start = self.starts_at.replace(tzinfo=LOCAL_TZ)
        else:
            local_start = self.starts_at.astimezone(LOCAL_TZ)
        return local_start.astimezone(timezone.utc)


class BookingResponse(BaseModel):
    booking_id: str
    status: str
    starts_at: datetime
    duration_minutes: int
    actual_duration_minutes: int | None = None
    deposit_required: bool
    deposit_cents: int | None = None
    deposit_policy: list[str]
    deposit_status: str | None = None
    checkout_url: str | None = None


class BookingCompletionRequest(BaseModel):
    actual_duration_minutes: int = Field(gt=0)


class BookingRescheduleRequest(BaseModel):
    starts_at: datetime
    time_on_site_hours: float = Field(gt=0)

    @property
    def duration_minutes(self) -> int:
        return round_duration_minutes(self.time_on_site_hours)


class AdminBookingListItem(BaseModel):
    booking_id: str
    lead_id: str | None
    starts_at: datetime
    duration_minutes: int
    status: str
    lead_name: str | None = None
    lead_email: str | None = None
