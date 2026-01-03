from datetime import datetime

from pydantic import BaseModel, ConfigDict


class OutboxEventResponse(BaseModel):
    event_id: str
    kind: str
    status: str
    attempts: int
    last_error: str | None
    next_attempt_at: datetime | None
    created_at: datetime
    dedupe_key: str

    model_config = ConfigDict(from_attributes=True)


class OutboxReplayResponse(BaseModel):
    event_id: str
    status: str
    next_attempt_at: datetime | None
    attempts: int
    last_error: str | None
