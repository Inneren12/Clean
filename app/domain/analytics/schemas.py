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


class FunnelCounts(BaseModel):
    leads: int
    bookings: int
    completed: int
    paid: int


class FunnelConversionRates(BaseModel):
    lead_to_booking: float
    booking_to_completed: float
    completed_to_paid: float
    lead_to_paid: float


class FunnelAnalyticsResponse(BaseModel):
    range_start: datetime
    range_end: datetime
    counts: FunnelCounts
    conversion_rates: FunnelConversionRates


class NpsDistribution(BaseModel):
    total_responses: int
    average_score: float | None
    promoters: int
    passives: int
    detractors: int
    promoter_rate: float
    passive_rate: float
    detractor_rate: float


class NpsTrendPoint(BaseModel):
    period_start: datetime
    average_score: float | None
    response_count: int


class NpsTrends(BaseModel):
    weekly: list[NpsTrendPoint]
    monthly: list[NpsTrendPoint]


class NpsAnalyticsResponse(BaseModel):
    range_start: datetime
    range_end: datetime
    distribution: NpsDistribution
    trends: NpsTrends


class CohortBreakdown(BaseModel):
    cohort_month: datetime
    customers: int
    repeat_customers: int
    repeat_rate: float


class CohortAnalyticsResponse(BaseModel):
    range_start: datetime
    range_end: datetime
    cohorts: list[CohortBreakdown]


class AdminMetricsResponse(BaseModel):
    range_start: datetime
    range_end: datetime
    conversions: ConversionMetrics
    revenue: RevenueMetrics
    accuracy: DurationAccuracy
    financial: FinancialKpis
    operational: OperationalKpis
