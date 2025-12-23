from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.domain.pricing.models import AddOns, CleaningType, EstimateRequest, EstimateResponse, Frequency


class UTMParams(BaseModel):
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    utm_term: Optional[str] = None
    utm_content: Optional[str] = None


class LeadStructuredInputs(EstimateRequest):
    model_config = ConfigDict(extra="forbid")

    @field_validator("cleaning_type", mode="before")
    @classmethod
    def default_cleaning_type(cls, value):
        return CleaningType.standard if value is None else value

    @field_validator("frequency", mode="before")
    @classmethod
    def default_frequency(cls, value):
        return Frequency.one_time if value is None else value

    @field_validator("heavy_grease", "multi_floor", mode="before")
    @classmethod
    def default_booleans(cls, value):
        return False if value is None else value

    @field_validator("add_ons", mode="before")
    @classmethod
    def default_add_ons(cls, value):
        return AddOns() if value is None else value


class LeadCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    structured_inputs: LeadStructuredInputs
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
