from __future__ import annotations
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.bot.nlu.models import Entities, Intent


def to_camel(string: str) -> str:
    parts = string.split("_")
    return parts[0] + "".join(word.capitalize() for word in parts[1:])


class BotBaseModel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel, extra="ignore", use_enum_values=True
    )


class ConversationStatus(str, Enum):
    active = "active"
    completed = "completed"
    handed_off = "handed_off"


class MessageRole(str, Enum):
    user = "user"
    bot = "bot"
    system = "system"


class FsmStep(str, Enum):
    collecting_requirements = "collecting_requirements"
    handoff_check = "handoff_check"
    scheduling = "scheduling"
    routing = "routing"
    support = "support"


class ConversationState(BotBaseModel):
    current_intent: Optional[Intent] = None
    fsm_step: Optional[FsmStep] = None
    filled_fields: Dict[str, Any] = Field(default_factory=dict)
    confidence: Optional[float] = None


class ConversationCreate(BotBaseModel):
    channel: str
    user_id: Optional[str] = None
    anon_id: Optional[str] = None
    state: ConversationState = Field(default_factory=ConversationState)


class ConversationRecord(ConversationCreate):
    conversation_id: str
    status: ConversationStatus = ConversationStatus.active
    created_at: float
    updated_at: float


class MessagePayload(BotBaseModel):
    role: MessageRole
    text: str
    intent: Optional[Intent] = None
    confidence: Optional[float] = None
    extracted_entities: Dict[str, Any] = Field(default_factory=dict)
    reasons: List[str] = Field(default_factory=list)


class MessageRecord(MessagePayload):
    message_id: str
    conversation_id: str
    created_at: float


class SessionCreateRequest(BotBaseModel):
    channel: str = "web"
    user_id: Optional[str] = None
    anon_id: Optional[str] = None


class SessionCreateResponse(BotBaseModel):
    conversation_id: str
    status: ConversationStatus
    state: ConversationState


class MessageRequest(BotBaseModel):
    conversation_id: str
    text: str
    user_id: Optional[str] = None
    anon_id: Optional[str] = None


class BotReply(BotBaseModel):
    text: str
    intent: Intent
    confidence: float
    state: ConversationState
    extracted_entities: Dict[str, Any] = Field(default_factory=dict)
    reasons: List[str] = Field(default_factory=list)


class MessageResponse(BotBaseModel):
    conversation_id: str
    reply: BotReply


class LeadPayload(BotBaseModel):
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


class CasePayload(BotBaseModel):
    reason: str
    summary: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    source_conversation_id: Optional[str] = None
    status: str = "open"


class CaseRecord(CasePayload):
    case_id: str
    created_at: float
