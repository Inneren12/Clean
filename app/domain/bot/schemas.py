from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ConversationStatus(str, Enum):
    active = "active"
    completed = "completed"
    handed_off = "handed_off"


class MessageRole(str, Enum):
    user = "user"
    bot = "bot"
    system = "system"


class ConversationState(BaseModel):
    current_intent: Optional[str] = None
    fsm_step: Optional[str] = None
    filled_fields: Dict[str, Any] = Field(default_factory=dict)
    confidence: Optional[float] = None


class ConversationCreate(BaseModel):
    channel: str
    user_id: Optional[str] = None
    anon_id: Optional[str] = None
    state: ConversationState = Field(default_factory=ConversationState)


class ConversationRecord(ConversationCreate):
    conversation_id: str
    status: ConversationStatus = ConversationStatus.active
    created_at: float
    updated_at: float


class MessagePayload(BaseModel):
    role: MessageRole
    text: str
    intent: Optional[str] = None
    confidence: Optional[float] = None
    extracted_entities: Dict[str, Any] = Field(default_factory=dict)


class MessageRecord(MessagePayload):
    message_id: str
    conversation_id: str
    created_at: float


class SessionCreateRequest(BaseModel):
    channel: str = "web"
    user_id: Optional[str] = None
    anon_id: Optional[str] = None


class SessionCreateResponse(BaseModel):
    conversation_id: str
    status: ConversationStatus
    state: ConversationState


class MessageRequest(BaseModel):
    conversation_id: str
    text: str
    user_id: Optional[str] = None
    anon_id: Optional[str] = None


class BotReply(BaseModel):
    text: str
    intent: str
    confidence: float
    state: ConversationState


class MessageResponse(BaseModel):
    conversation_id: str
    reply: BotReply


class LeadPayload(BaseModel):
    service_type: Optional[str] = None
    property_type: Optional[str] = None
    size: Optional[str] = None
    condition: Optional[str] = None
    extras: List[str] = Field(default_factory=list)
    area: Optional[str] = None
    preferred_time_window: Optional[str] = None
    contact: Dict[str, str] = Field(default_factory=dict)
    price_estimate: Optional[Dict[str, Any]] = None
    duration_estimate_min: Optional[int] = None
    source_conversation_id: Optional[str] = None
    status: str = "new"


class LeadRecord(LeadPayload):
    lead_id: str
    created_at: float


class CasePayload(BaseModel):
    reason: str
    summary: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    source_conversation_id: Optional[str] = None
    status: str = "open"


class CaseRecord(CasePayload):
    case_id: str
    created_at: float

