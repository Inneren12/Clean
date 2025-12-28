from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

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
    awaiting_field: Optional[str] = None
    cleaning_type: Optional[CleaningType] = None
    heavy_grease: Optional[bool] = None
    multi_floor: Optional[bool] = None
    frequency: Optional[Frequency] = None
    add_ons: AddOns = Field(default_factory=AddOns)


class SessionState(BaseModel):
    session_id: str
    brand: str = "economy"
    fields: ParsedFields


class ChatTurnRequest(BaseModel):
    session_id: str
    message: str
    brand: str = "economy"
    channel: str = "web"
    client_context: Optional[Dict[str, object]] = None


# UI Contract Extension Models (S2-A)
class Choice(BaseModel):
    """Represents a selectable option in the chat UI (button/chip)."""
    id: str  # Unique identifier for this choice
    label: str  # Display text
    value: Optional[str] = None  # Value to send when selected (defaults to label)


class ChoicesConfig(BaseModel):
    """Configuration for choice-based UI controls."""
    items: List[Choice]  # Available options
    multi_select: bool = False  # Allow multiple selections
    selection_type: Literal["button", "chip"] = "chip"  # UI style hint


class StepInfo(BaseModel):
    """Progress information for form-style conversations."""
    current_step: conint(ge=1)  # Current step number (1-indexed)
    total_steps: conint(ge=1)  # Total number of steps
    step_label: Optional[str] = None  # Label for current step (e.g., "Property Details")
    remaining_questions: Optional[conint(ge=0)] = None  # Estimated questions left


class SummaryField(BaseModel):
    """Editable field in the conversation summary."""
    key: str  # Field identifier (e.g., "beds", "cleaning_type")
    label: str  # Display label (e.g., "Bedrooms")
    value: Union[str, int, float, bool, None]  # Current value (primitive types only)
    editable: bool = True  # Whether user can edit this field
    field_type: Literal["text", "number", "select", "boolean"] = "text"  # Input type
    options: Optional[List[Choice]] = None  # For select type fields


class SummaryPatch(BaseModel):
    """Structured summary that user can review and edit."""
    title: Optional[str] = "Conversation Summary"  # Summary section title
    fields: List[SummaryField]  # Editable fields


class UIHint(BaseModel):
    """UI behavior hints for the frontend."""
    show_summary: Optional[bool] = None  # Display summary panel
    show_confirm: Optional[bool] = None  # Show confirmation button
    show_choices: Optional[bool] = None  # Render choices if available
    show_progress: Optional[bool] = None  # Display step progress indicator
    minimize_text: Optional[bool] = None  # De-emphasize text in favor of structured UI


class ChatTurnResponse(BaseModel):
    session_id: str
    intent: Intent
    parsed_fields: ParsedFields
    state: Dict[str, object] = Field(default_factory=dict)
    missing_fields: List[str]
    proposed_questions: List[str] = Field(default_factory=list)
    reply_text: str
    handoff_required: bool
    estimate: Optional[EstimateResponse] = None
    confidence: float = 0.0

    # UI Contract Extension (S2-A) - all optional for backward compatibility
    choices: Optional[ChoicesConfig] = None  # Selectable options (chips/buttons)
    step_info: Optional[StepInfo] = None  # Form progress indicator
    summary_patch: Optional[SummaryPatch] = None  # Editable summary fields
    ui_hint: Optional[UIHint] = None  # UI behavior hints
