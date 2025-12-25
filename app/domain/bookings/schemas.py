from datetime import date, datetime, timezone

from pydantic import BaseModel, Field

from app.domain.bookings.service import round_duration_minutes


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
        if self.starts_at.tzinfo is None:
            return self.starts_at.replace(tzinfo=timezone.utc)
        return self.starts_at.astimezone(timezone.utc)


class BookingResponse(BaseModel):
    booking_id: str
    status: str
    starts_at: datetime
    duration_minutes: int
