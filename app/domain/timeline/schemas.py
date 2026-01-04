"""Schemas for unified timeline view."""

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class TimelineEvent(BaseModel):
    """Single timeline event (audit log, outbox event, payment, photo review, etc.)."""

    event_id: str
    event_type: Literal[
        "audit_log",
        "email_sent",
        "payment_received",
        "photo_reviewed",
        "status_changed",
        "worker_assigned",
        "booking_moved",
        "nps_response",
        "support_ticket",
        "outbox_event",
    ]
    timestamp: datetime
    actor: str | None = None  # Who performed the action
    action: str  # Human-readable description
    resource_type: str | None = None
    resource_id: str | None = None
    before: dict | None = Field(default=None, description="State before change")
    after: dict | None = Field(default=None, description="State after change")
    metadata: dict = Field(default_factory=dict, description="Additional context")


class TimelineResponse(BaseModel):
    """Chronological timeline for a resource."""

    resource_type: str  # "booking", "invoice", "order"
    resource_id: str
    events: list[TimelineEvent]
    total: int
