from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ExportEventResponse(BaseModel):
    event_id: str
    lead_id: Optional[str] = None
    mode: str
    target_url_host: Optional[str] = None
    attempts: int
    last_error_code: Optional[str] = None
    created_at: datetime
