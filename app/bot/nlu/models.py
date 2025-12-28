from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.shared.naming import to_camel


class Intent(str, Enum):
    booking = "booking"
    price = "price"
    scope = "scope"
    reschedule = "reschedule"
    cancel = "cancel"
    status = "status"
    faq = "faq"
    human = "human"
    complaint = "complaint"


class TimeWindow(BaseModel):
    start: Optional[str] = None
    end: Optional[str] = None
    label: Optional[str] = None
    day: Optional[str] = None


class Entities(BaseModel):
    beds: Optional[int] = None
    baths: Optional[int] = None
    square_feet: Optional[int] = Field(default=None, alias="squareFeet")
    square_meters: Optional[int] = Field(default=None, alias="squareMeters")
    service_type: Optional[str] = Field(default=None, alias="serviceType")
    extras: List[str] = Field(default_factory=list)
    time_window: Optional[TimeWindow] = Field(default=None, alias="timeWindow")
    area: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)


class IntentResult(BaseModel):
    intent: Intent
    confidence: float
    reasons: List[str] = Field(default_factory=list)
    entities: Entities = Field(default_factory=Entities)

    model_config = ConfigDict(populate_by_name=True)
