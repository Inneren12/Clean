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


class FinancialKpis(BaseModel):
    total_revenue_cents: int
    revenue_per_day_cents: float
    margin_cents: int
    average_order_value_cents: float | None


class OperationalKpis(BaseModel):
    crew_utilization: float | None
    cancellation_rate: float
    retention_30_day: float
    retention_60_day: float
    retention_90_day: float


class AdminMetricsResponse(BaseModel):
    range_start: datetime
    range_end: datetime
    conversions: ConversionMetrics
    revenue: RevenueMetrics
    accuracy: DurationAccuracy
    financial: FinancialKpis
    operational: OperationalKpis
