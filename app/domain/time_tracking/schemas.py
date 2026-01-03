from datetime import datetime

from pydantic import BaseModel


class TimeSegment(BaseModel):
    start: datetime
    end: datetime | None = None


class TimeTrackingResponse(BaseModel):
    booking_id: str
    entry_id: str | None
    state: str | None = None
    started_at: datetime | None = None
    paused_at: datetime | None = None
    finished_at: datetime | None = None
    check_in_at: datetime | None = None
    check_out_at: datetime | None = None
    planned_minutes: int | None = None
    planned_seconds: int | None = None
    total_seconds: int
    effective_seconds: int
    delta_seconds: int | None = None
    leak_flag: bool = False
    planned_vs_actual_ratio: float | None = None
    proof_required: bool | None = None
    proof_attached: bool = False
    segments: list[TimeSegment]
