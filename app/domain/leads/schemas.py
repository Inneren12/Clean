from typing import Any, Dict, List, Optional

from pydantic import BaseModel, EmailStr, Field

from app.domain.pricing.models import EstimateResponse


class UTMParams(BaseModel):
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    utm_term: Optional[str] = None
    utm_content: Optional[str] = None


class LeadCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    phone: str = Field(..., min_length=1)
    email: Optional[EmailStr] = None
    postal_code: Optional[str] = None
    address: Optional[str] = None
    preferred_dates: List[str] = Field(default_factory=list)
    access_notes: Optional[str] = None
    parking: Optional[str] = None
    pets: Optional[str] = None
    allergies: Optional[str] = None
    notes: Optional[str] = None
    structured_inputs: Dict[str, Any]
    estimate_snapshot: EstimateResponse
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    utm_term: Optional[str] = None
    utm_content: Optional[str] = None
    utm: Optional[UTMParams] = None
    referrer: Optional[str] = None


class LeadResponse(BaseModel):
    lead_id: str
    next_step_text: str
