from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


TicketStatus = Literal["OPEN", "IN_PROGRESS", "RESOLVED"]


class TicketResponse(BaseModel):
    id: str
    order_id: str
    client_id: str | None
    status: TicketStatus
    priority: str
    subject: str
    body: str
    created_at: datetime
    updated_at: datetime


class TicketListResponse(BaseModel):
    tickets: list[TicketResponse]


class TicketUpdateRequest(BaseModel):
    status: TicketStatus = Field(..., description="New status for the ticket")
