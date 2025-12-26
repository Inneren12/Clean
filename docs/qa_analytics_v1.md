# QA Playbook: Analytics v1

This guide covers exercising the Sprint F analytics flow end to end: creating a lead, booking, confirming/completing, and verifying metrics output.

## Generating events

1. **Create a lead**
   ```bash
   curl -X POST http://localhost:8000/v1/leads \
     -H "Content-Type: application/json" \
     -d '{
       "name": "QA User",
       "phone": "780-555-0000",
       "email": "qa@example.com",
       "preferred_dates": ["Sat"],
       "structured_inputs": {"beds": 2, "baths": 1, "cleaning_type": "standard"},
       "estimate_snapshot": {
         "pricing_config_id": "economy",
         "pricing_config_version": "v1",
         "config_hash": "sha256:demo",
         "time_on_site_hours": 2.0,
         "total_before_tax": 250.00
       },
       "utm_source": "qa"
     }'
   ```

2. **Create a booking**
   ```bash
   curl -X POST http://localhost:8000/v1/bookings \
     -H "Content-Type: application/json" \
     -d '{
       "starts_at": "2025-01-10T17:00:00Z",
       "time_on_site_hours": 2.0,
       "lead_id": "<LEAD_ID_FROM_PREVIOUS_STEP>"
     }'
   ```

3. **Confirm the booking (admin/dispatcher)**
   ```bash
   curl -X POST http://localhost:8000/v1/admin/bookings/<BOOKING_ID>/confirm \
     -u admin:secret
   ```

4. **Complete the booking (admin/dispatcher)**
   ```bash
   curl -X POST http://localhost:8000/v1/admin/bookings/<BOOKING_ID>/complete \
     -u admin:secret \
     -H "Content-Type: application/json" \
     -d '{"actual_duration_minutes": 150}'
   ```

## Querying metrics (/v1/admin/metrics)

*JSON*
```bash
curl "http://localhost:8000/v1/admin/metrics?from=2024-01-01T00:00:00Z&to=2026-01-01T00:00:00Z" \
  -u admin:secret
```

*CSV*
```bash
curl "http://localhost:8000/v1/admin/metrics?from=2024-01-01T00:00:00Z&to=2026-01-01T00:00:00Z&format=csv" \
  -u admin:secret
```

## Verification checklist

- Endpoints return `201` for `/v1/leads` and `/v1/bookings` even if analytics logging is unavailable (check logs for `analytics_log_failed`).
- `/v1/admin/metrics` requires admin credentials; dispatch-only credentials should receive `403`.
- Conversion counts include `lead_created`, `booking_created`, `booking_confirmed`, and `job_completed`; repeated confirmation calls do **not** increment the confirmed count.
- Duration accuracy reflects the completed booking and `actual_duration_minutes` payload.
- CSV export returns `text/csv` with the same metrics fields as JSON.
