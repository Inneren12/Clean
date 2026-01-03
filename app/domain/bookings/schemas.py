from datetime import date, datetime, timezone
from datetime import date, datetime, time, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.bookings.policy import BookingPolicySnapshot
from app.domain.bookings.service import (
    DEFAULT_SLOT_DURATION_MINUTES,
    LOCAL_TZ,
    TimeWindowPreference,
    apply_duration_constraints,
    round_duration_minutes,
)
from app.domain.pricing.models import CleaningType


class SlotAvailabilityResponse(BaseModel):
    date: date
    duration_minutes: int
    slots: list[datetime]
    clarifier: str | None = None


class SlotQuery(BaseModel):
    date: date
    time_on_site_hours: float = Field(gt=0)
    postal_code: str | None = None
    service_type: CleaningType | None = None
    window_start_hour: int | None = Field(None, ge=0, le=23)
    window_end_hour: int | None = Field(None, ge=1, le=24)

    @property
    def duration_minutes(self) -> int:
        rounded = round_duration_minutes(self.time_on_site_hours)
        return apply_duration_constraints(rounded, self.service_type)

    def time_window(self) -> TimeWindowPreference | None:
        if self.window_start_hour is None or self.window_end_hour is None:
            return None
        return TimeWindowPreference(start_hour=self.window_start_hour, end_hour=self.window_end_hour)

    @model_validator(mode="after")
    def validate_window(self) -> "SlotQuery":
        if (self.window_start_hour is None) ^ (self.window_end_hour is None):
            raise ValueError("window_start_hour and window_end_hour must both be provided")
        if self.window_start_hour is not None and self.window_end_hour is not None:
            if self.window_end_hour <= self.window_start_hour:
                raise ValueError("window_end_hour must be greater than window_start_hour")
        return self


class BookingCreateRequest(BaseModel):
    starts_at: datetime
    time_on_site_hours: float = Field(gt=0)
    lead_id: str | None = None
    service_type: CleaningType | None = None

    @property
    def duration_minutes(self) -> int:
        rounded = round_duration_minutes(self.time_on_site_hours)
        return apply_duration_constraints(rounded, self.service_type)

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
    policy_snapshot: BookingPolicySnapshot | None = None
    risk_score: int
    risk_band: str
    risk_reasons: list[str]
    cancellation_exception: bool = False
    cancellation_exception_note: str | None = None


class BookingCompletionRequest(BaseModel):
    actual_duration_minutes: int = Field(gt=0)


class BookingRescheduleRequest(BaseModel):
    starts_at: datetime
    time_on_site_hours: float = Field(gt=0)
    service_type: CleaningType | None = None

    @property
    def duration_minutes(self) -> int:
        rounded = round_duration_minutes(self.time_on_site_hours)
        return apply_duration_constraints(rounded, self.service_type)


class AdminBookingListItem(BaseModel):
    booking_id: str
    lead_id: str | None
    starts_at: datetime
    duration_minutes: int
    status: str
    lead_name: str | None = None
    lead_email: str | None = None


class PhotoPhase(str, Enum):
    BEFORE = "BEFORE"
    AFTER = "AFTER"

    @classmethod
    def from_any_case(cls, value: str) -> "PhotoPhase":
        try:
            return cls(value.upper())
        except Exception as exc:  # noqa: BLE001
            raise ValueError("phase must be BEFORE or AFTER") from exc


class OrderPhotoResponse(BaseModel):
    photo_id: str
    order_id: str
    phase: PhotoPhase
    filename: str
    original_filename: str | None = None
    content_type: str
    size_bytes: int
    sha256: str
    uploaded_by: str
    created_at: datetime


class OrderPhotoListResponse(BaseModel):
    photos: list[OrderPhotoResponse]


class SignedUrlResponse(BaseModel):
    url: str
    expires_at: datetime
    expires_in: int


class ConsentPhotosUpdateRequest(BaseModel):
    consent_photos: bool


class ConsentPhotosResponse(BaseModel):
    order_id: str
    consent_photos: bool


class WorkingHoursUpdateRequest(BaseModel):
    team_id: int = Field(1, ge=1)
    day_of_week: int = Field(ge=0, le=6)
    start_time: time
    end_time: time

    @model_validator(mode="after")
    def ensure_window(self) -> "WorkingHoursUpdateRequest":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class WorkingHoursResponse(BaseModel):
    id: int
    team_id: int
    day_of_week: int
    start_time: time
    end_time: time


class BlackoutCreateRequest(BaseModel):
    team_id: int = Field(1, ge=1)
    starts_at: datetime
    ends_at: datetime
    reason: str | None = None

    @model_validator(mode="after")
    def ensure_bounds(self) -> "BlackoutCreateRequest":
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class BlackoutResponse(BaseModel):
    id: int
    team_id: int
    starts_at: datetime
    ends_at: datetime
    reason: str | None = None


class ClientSlotQuery(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    team_id: int | None = Field(None, ge=1)
    start: datetime = Field(alias="from")
    end: datetime = Field(alias="to")
    duration_minutes: int = Field(DEFAULT_SLOT_DURATION_MINUTES, gt=0)

    @model_validator(mode="after")
    def validate_bounds(self) -> "ClientSlotQuery":
        if self.end <= self.start:
            raise ValueError("to must be after from")
        return self


class ClientBookingRequest(BaseModel):
    starts_at: datetime
    duration_minutes: int = Field(DEFAULT_SLOT_DURATION_MINUTES, gt=0)
    lead_id: str | None = None
    team_id: int | None = Field(None, ge=1)
    service_type: CleaningType | None = None

    def normalized_start(self) -> datetime:
        local_start = self.starts_at
        if self.starts_at.tzinfo is None:
            local_start = self.starts_at.replace(tzinfo=LOCAL_TZ)
        else:
            local_start = self.starts_at.astimezone(LOCAL_TZ)
        return local_start.astimezone(timezone.utc)


class ClientRescheduleRequest(BaseModel):
    starts_at: datetime
    duration_minutes: int = Field(DEFAULT_SLOT_DURATION_MINUTES, gt=0)
    service_type: CleaningType | None = None

    def normalized_start(self) -> datetime:
        local_start = self.starts_at
        if self.starts_at.tzinfo is None:
            local_start = self.starts_at.replace(tzinfo=LOCAL_TZ)
        else:
            local_start = self.starts_at.astimezone(LOCAL_TZ)
        return local_start.astimezone(timezone.utc)


class ClientBookingResponse(BaseModel):
    booking_id: str
    status: str
    starts_at: datetime
    duration_minutes: int
    lead_id: str | None = None
    policy_snapshot: BookingPolicySnapshot | None = None
    deposit_required: bool
    deposit_cents: int | None = None
    deposit_policy: list[str]
    deposit_status: str | None = None
    cancellation_exception: bool = False


class ClientSlotAvailabilityResponse(BaseModel):
    duration_minutes: int
    slots: list[datetime]
