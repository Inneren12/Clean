# Sprint 1 QA Script

## Quick curl checks

```bash
curl http://localhost:8000/healthz
```

Expected:

```json
{"status":"ok"}
```

```bash
curl -X POST http://localhost:8000/v1/estimate \
  -H "Content-Type: application/json" \
  -d '{
    "beds": 2,
    "baths": 1.5,
    "cleaning_type": "deep",
    "heavy_grease": true,
    "multi_floor": true,
    "frequency": "weekly",
    "add_ons": {
      "oven": true,
      "fridge": false,
      "microwave": true,
      "cabinets": false,
      "windows_up_to_5": true,
      "balcony": false,
      "linen_beds": 2,
      "steam_armchair": 0,
      "steam_sofa_2": 1,
      "steam_sofa_3": 0,
      "steam_sectional": 0,
      "steam_mattress": 0,
      "carpet_spot": 1
    }
  }'
```

Expected:

- HTTP 200
- includes `pricing_config_id`, `pricing_config_version`, `config_hash`
- includes `team_size`, `time_on_site_hours`, `total_before_tax`

```bash
curl -X POST http://localhost:8000/v1/chat/turn \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "session-qa",
    "message": "Deep clean 2 bed 1.5 bath with oven and fridge weekly"
  }'
```

Expected:

- HTTP 200
- `reply_text` present
- `state` is JSON-safe and includes normalized fields
- `estimate` present when required fields are complete

## Dialogue scripts (2–4 turns each)

1) **Estimate produced**
   - User: “Need a deep clean 2 bed 2 bath oven + fridge biweekly”
   - Bot: returns estimate, `handoff_required=false`

2) **Estimate produced (standard default)**
   - User: “Standard cleaning 1 bed 1 bath”
   - Bot: returns estimate, `cleaning_type=standard`

3) **Missing fields prompt**
   - User: “Looking to book cleaning”
   - Bot: asks for missing beds/baths
   - User: “2 bed 1 bath”
   - Bot: returns estimate

4) **Missing fields prompt (add-ons)**
   - User: “Need oven cleaning”
   - Bot: asks for beds/baths
   - User: “3 bed 2 bath”
   - Bot: returns estimate with add-on cost

5) **Recurring discount**
   - User: “Deep clean 2 bed 1 bath weekly”
   - Bot: returns estimate with discount applied

6) **Red-flag handoff**
   - User: “Mold and renovation dust cleanup”
   - Bot: `handoff_required=true`, `estimate=null`

## Checklist

- ProblemDetails includes `request_id` and `errors[]` on validation failures.
- Logs do not contain raw PII (email/phone/address).
- Chat `state` is JSON-safe (no Enum objects).
- Web quick replies prefill the input and do not auto-send.
