from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, conint

from app.domain.pricing.models import AddOns, CleaningType, Frequency, EstimateResponse


class Intent(str, Enum):
    quote = "QUOTE"
    book = "BOOK"
    faq = "FAQ"
    change_cancel = "CHANGE_CANCEL"
    complaint = "COMPLAINT"
    other = "OTHER"


class ParsedFields(BaseModel):
    beds: Optional[conint(ge=0, le=10)] = None
    baths: Optional[float] = None
    cleaning_type: Optional[CleaningType] = None
    heavy_grease: Optional[bool] = None
    multi_floor: Optional[bool] = None
    frequency: Optional[Frequency] = None
    add_ons: AddOns = Field(default_factory=AddOns)


class ChatTurnRequest(BaseModel):
    session_id: str
    message: str


class ChatTurnResponse(BaseModel):
    session_id: str
    intent: Intent
    parsed_fields: ParsedFields
    missing_fields: List[str]
    proposed_questions: List[str]
    reply_text: str
    handoff_required: bool
    estimate: Optional[EstimateResponse] = None
    confidence: float = 0.0
