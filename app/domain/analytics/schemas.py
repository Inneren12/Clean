from datetime import datetime

from pydantic import BaseModel


class ConversionMetrics(BaseModel):
    lead_created: int
    booking_created: int
    booking_confirmed: int
    job_completed: int


class RevenueMetrics(BaseModel):
    average_estimated_revenue_cents: float | None


class DurationAccuracy(BaseModel):
    sample_size: int
    average_estimated_duration_minutes: float | None
    average_actual_duration_minutes: float | None
    average_delta_minutes: float | None


class AdminMetricsResponse(BaseModel):
    range_start: datetime
    range_end: datetime
    conversions: ConversionMetrics
    revenue: RevenueMetrics
    accuracy: DurationAccuracy
