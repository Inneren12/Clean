from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, conint, confloat, ConfigDict

# Re-export PricingConfig for backward compatibility
# (actual implementation in config_loader.py)
from app.domain.pricing.config_loader import PricingConfig

__all__ = [
    "PricingConfig",
    "CleaningType",
    "Frequency",
    "AddOns",
    "EstimateRequest",
    "EstimateBreakdown",
    "EstimateResponse",
]


class CleaningType(str, Enum):
    standard = "standard"
    deep = "deep"
    move_out_empty = "move_out_empty"
    move_in_empty = "move_in_empty"


class Frequency(str, Enum):
    one_time = "one_time"
    weekly = "weekly"
    biweekly = "biweekly"
    monthly = "monthly"


class AddOns(BaseModel):
    model_config = ConfigDict(extra="forbid")

    oven: bool = False
    fridge: bool = False
    microwave: bool = False
    cabinets: bool = False
    windows_up_to_5: bool = False
    balcony: bool = False
    linen_beds: conint(ge=0) = 0
    steam_armchair: conint(ge=0) = 0
    steam_sofa_2: conint(ge=0) = 0
    steam_sofa_3: conint(ge=0) = 0
    steam_sectional: conint(ge=0) = 0
    steam_mattress: conint(ge=0) = 0
    carpet_spot: conint(ge=0) = 0


class EstimateRequest(BaseModel):
    beds: conint(ge=0, le=10)
    baths: confloat(ge=0.0, le=10.0)
    cleaning_type: CleaningType = CleaningType.standard
    heavy_grease: bool = False
    multi_floor: bool = False
    frequency: Optional[Frequency] = Frequency.one_time
    add_ons: AddOns = Field(default_factory=AddOns)


class EstimateBreakdown(BaseModel):
    base_hours: float
    multiplier: float
    extra_hours: float
    total_cleaner_hours: float
    min_cleaner_hours_applied: float
    team_size: int
    time_on_site_hours: float
    billed_cleaner_hours: float
    labor_cost: float
    add_ons_cost: float
    discount_amount: float
    total_before_tax: float


class EstimateResponse(BaseModel):
    pricing_config_id: str
    pricing_config_version: str
    config_hash: str
    rate: float
    team_size: int
    time_on_site_hours: float
    billed_cleaner_hours: float
    labor_cost: float
    discount_amount: float
    add_ons_cost: float
    total_before_tax: float
    assumptions: List[str] = Field(default_factory=list)
    missing_info: List[str] = Field(default_factory=list)
    confidence: float
    breakdown: Optional[EstimateBreakdown] = None
