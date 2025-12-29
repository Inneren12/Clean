# KPI Formulas

The `/v1/admin/metrics` endpoint now returns server-side KPI aggregates computed directly from SQL queries. All calculations respect the supplied `from` / `to` range (UTC, inclusive) and ignore rows outside the window.

## Financial KPIs
- **Total revenue (cents):** Sum of `base_charge_cents - refund_total_cents - credit_note_total_cents` for `DONE` bookings with `starts_at` in range.
- **Revenue per day (cents):** `total_revenue_cents / max(1, ceil(range_seconds / 86_400))`.
- **Average order value (cents):** `total_revenue_cents / completed_booking_count` when at least one booking is completed.
- **Margin (cents):** `total_revenue_cents - labor_cost_cents`, where labor cost comes from each booking’s linked lead `estimate_snapshot.labor_cost` (converted to cents).

## Operational KPIs
- **Crew utilization:** `sum(actual_seconds) / sum(planned_seconds)` where planned seconds come from `coalesce(planned_minutes, duration_minutes) * 60` and actual seconds prefer `actual_seconds` then `actual_duration_minutes * 60`.
- **Cancellation rate:** `cancelled_bookings / total_bookings` for all bookings in range.
- **Retention 30/60/90:** Distinct customers (by `coalesce(client_id, lead_id)`) with a completed booking in range **and** a previous completed booking within the last 30/60/90 days, divided by distinct customers with a completed booking in range.

## CSV Export
The CSV response includes every KPI alongside the existing conversion and accuracy metrics so downloads mirror the JSON payload.
