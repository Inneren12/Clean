from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ExportEventResponse(BaseModel):
    event_id: str
    lead_id: Optional[str] = None
    mode: str
    target_url: Optional[str] = None
    target_url_host: Optional[str] = None
    payload: Optional[dict] = None
    attempts: int
    last_error_code: Optional[str] = None
    created_at: datetime
    replay_count: int
    last_replayed_at: Optional[datetime] = None
    last_replayed_by: Optional[str] = None


class ExportDeadLetterListResponse(BaseModel):
    items: list[ExportEventResponse]
    total: int


class ExportReplayResponse(BaseModel):
    event_id: str
    success: bool
    attempts: int
    last_error_code: Optional[str] = None
    last_replayed_at: datetime
    last_replayed_by: str
