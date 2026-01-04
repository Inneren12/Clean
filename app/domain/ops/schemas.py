from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class JobStatusResponse(BaseModel):
    name: str
    last_heartbeat: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    last_error: Optional[str] = None
    last_error_at: Optional[datetime] = None
    consecutive_failures: int


class QuickActionModel(BaseModel):
    label: str
    target: str
    method: str = "GET"


class GlobalSearchResult(BaseModel):
    kind: str
    ref: str
    label: str
    status: str | None = None
    created_at: datetime
    relevance_score: int = 0
    quick_actions: list[QuickActionModel] = []
    model_config = ConfigDict(from_attributes=True)


class ScheduleBooking(BaseModel):
    booking_id: str
    starts_at: datetime
    duration_minutes: int
    status: str
    model_config = ConfigDict(from_attributes=True)


class ScheduleBlackout(BaseModel):
    starts_at: datetime
    ends_at: datetime
    reason: str | None = None
    model_config = ConfigDict(from_attributes=True)


class ScheduleResponse(BaseModel):
    team_id: int
    day: date
    bookings: list[ScheduleBooking]
    blackouts: list[ScheduleBlackout]
    available_slots: list[datetime]
    model_config = ConfigDict(from_attributes=True)


class MoveBookingRequest(BaseModel):
    starts_at: datetime
    duration_minutes: int | None = None
    team_id: int | None = None


class BlockSlotRequest(BaseModel):
    starts_at: datetime
    ends_at: datetime
    reason: str | None = None
    team_id: int | None = None


class SuggestedTeam(BaseModel):
    team_id: int
    name: str


class SuggestedWorker(BaseModel):
    worker_id: int
    name: str
    team_id: int
    team_name: str


class ScheduleSuggestions(BaseModel):
    teams: list[SuggestedTeam]
    workers: list[SuggestedWorker]


class ConflictDetail(BaseModel):
    kind: str
    reference: str
    starts_at: datetime
    ends_at: datetime
    note: str | None = None


class ConflictCheckResponse(BaseModel):
    has_conflict: bool
    conflicts: list[ConflictDetail]


class BulkBookingsRequest(BaseModel):
    booking_ids: list[str]
    team_id: int | None = None
    status: str | None = None
    send_reminder: bool = False


class BulkBookingsResponse(BaseModel):
    updated: int
    reminders_sent: int


class TemplatePreviewRequest(BaseModel):
    template: str
    booking_id: str | None = None
    invoice_id: str | None = None
    lead_id: str | None = None


class TemplatePreviewResponse(BaseModel):
    template: str
    version: str
    subject: str
    body: str
