# Sprint 2 E2E Checklist (Chat → Estimate → Lead)

## Preconditions

- API running: `make up`
- Migrations applied: `make migrate`
- Postgres reachable via Docker (`make psql`)

## Step-by-step flow

1. **Start a chat session**

   ```bash
   curl -X POST http://localhost:8000/v1/chat/turn \
     -H "Content-Type: application/json" \
     -d '{
       "session_id": "qa-session-001",
       "message": "Need a deep clean"
     }'
   ```

2. **Provide beds/baths to get an estimate**

   ```bash
   curl -X POST http://localhost:8000/v1/chat/turn \
     -H "Content-Type: application/json" \
     -d '{
       "session_id": "qa-session-001",
       "message": "2 bed 2 bath with oven"
     }'
   ```

   Confirm the response includes `estimate` with:

   - `pricing_config_id`
   - `pricing_config_version`
   - `config_hash` prefixed with `sha256:`

3. **Submit a lead**

   ```bash
   curl -X POST http://localhost:8000/v1/leads \
     -H "Content-Type: application/json" \
     -d '{
       "name": "Taylor QA",
       "phone": "780-555-7777",
       "preferred_dates": ["Sat afternoon", "Sun morning"],
       "structured_inputs": {
         "beds": 2,
         "baths": 2,
         "cleaning_type": "deep"
       },
       "estimate_snapshot": {
         "pricing_config_id": "economy",
         "pricing_config_version": "v1",
         "config_hash": "sha256:...",
         "rate": 35.0,
         "team_size": 2,
         "time_on_site_hours": 3.5,
         "billed_cleaner_hours": 7.0,
         "labor_cost": 245.0,
         "discount_amount": 12.25,
         "add_ons_cost": 50.0,
         "total_before_tax": 282.75,
         "assumptions": [],
         "missing_info": [],
         "confidence": 1.0
       }
     }'
   ```

## Verification

1. **Confirm chat session persistence**

   ```bash
   make psql
   ```

   ```sql
   SELECT session_id, state_json FROM chat_sessions WHERE session_id = 'qa-session-001';
   ```

2. **Confirm lead snapshot**

   ```sql
   SELECT lead_id, pricing_config_version, config_hash
   FROM leads
   ORDER BY created_at DESC
   LIMIT 1;
   ```

3. **Confirm estimate snapshot metadata is present**

   ```sql
   SELECT
     estimate_snapshot->>'pricing_config_version' AS pricing_config_version,
     estimate_snapshot->>'config_hash' AS config_hash
   FROM leads
   ORDER BY created_at DESC
   LIMIT 1;
   ```

4. **Verify logs do not expose PII**

   - Check application logs for redacted phone/email/address strings.
   - Ensure no raw contact details appear in structured logs.

## Optional: Host-based DB inspection

If you want to use host `psql`, point it at localhost:

```bash
psql postgresql://postgres:postgres@localhost:5432/cleaning
```
