"""Schemas for operator work queues."""

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class QuickActionItem(BaseModel):
    """Quick action button for queue items."""

    label: str
    target: str
    method: Literal["GET", "POST", "PUT", "DELETE"] = "GET"


class PhotoQueueItem(BaseModel):
    """Photo requiring review."""

    photo_id: str
    order_id: str
    booking_ref: str | None = None
    worker_name: str | None = None
    phase: str
    review_status: str
    needs_retake: bool
    uploaded_at: datetime
    filename: str
    content_type: str
    size_bytes: int
    quick_actions: list[QuickActionItem] = Field(default_factory=list)


class PhotoQueueResponse(BaseModel):
    """Photo queue listing."""

    items: list[PhotoQueueItem]
    total: int
    pending_count: int
    needs_retake_count: int


class InvoiceQueueItem(BaseModel):
    """Invoice requiring attention."""

    invoice_id: str
    invoice_number: str
    order_id: str | None = None
    customer_name: str | None = None
    customer_email: str | None = None
    status: str
    due_date: datetime | None = None
    total_cents: int
    currency: str
    days_overdue: int | None = None
    created_at: datetime
    quick_actions: list[QuickActionItem] = Field(default_factory=list)


class InvoiceQueueResponse(BaseModel):
    """Invoice queue listing."""

    items: list[InvoiceQueueItem]
    total: int
    overdue_count: int
    unpaid_count: int


class AssignmentQueueItem(BaseModel):
    """Unassigned booking."""

    booking_id: str
    lead_name: str | None = None
    lead_phone: str | None = None
    lead_email: str | None = None
    starts_at: datetime
    duration_minutes: int
    status: str
    team_name: str
    created_at: datetime
    days_until_start: int
    quick_actions: list[QuickActionItem] = Field(default_factory=list)


class AssignmentQueueResponse(BaseModel):
    """Assignment queue listing."""

    items: list[AssignmentQueueItem]
    total: int
    urgent_count: int  # Within 24h


class DLQItem(BaseModel):
    """Dead letter queue item."""

    event_id: str
    kind: str  # "outbox" or "export"
    event_type: str  # email/webhook/export type
    org_id: str
    status: str
    attempts: int
    last_error: str | None = None
    created_at: datetime
    payload_summary: str
    quick_actions: list[QuickActionItem] = Field(default_factory=list)


class DLQResponse(BaseModel):
    """Dead letter queue listing."""

    items: list[DLQItem]
    total: int
    outbox_dead_count: int
    export_dead_count: int
