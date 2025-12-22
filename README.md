# Cleaning Economy Bot API

FastAPI backend for the Economy MVP pricing and chat system.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Run locally

```bash
uvicorn app.main:app --reload
```

## Run with Docker

```bash
docker compose up --build
```

## Sprint 1 Notes / Boundaries

- Sprint 1 uses in-memory chat sessions only (no DB persistence yet).
- The Postgres container is included for Sprint 2 readiness; runtime logic does not depend on it yet.
- Alembic migrations are present but not applied until Sprint 2.
- `/v1/leads` is part of Sprint 2 and not implemented in Sprint 1.

## Web UI (chat tester)

The minimal Next.js chat UI lives in `web/`. It expects the API base URL in an
environment variable.

```bash
cd web
cp .env.example .env.local
npm install
npm run dev
```

Environment:

```
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

Quick replies in the UI prefill the input so users can edit before sending.

## Health check

```bash
curl http://localhost:8000/healthz
```

## Estimate API

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

Sample response:

```json
{
  "pricing_config_id": "economy_v1",
  "pricing_config_version": 1,
  "config_hash": "...",
  "rate": 35.0,
  "team_size": 2,
  "time_on_site_hours": 2.5,
  "billed_cleaner_hours": 5.0,
  "labor_cost": 175.0,
  "discount_amount": 17.5,
  "add_ons_cost": 90.0,
  "total_before_tax": 247.5,
  "assumptions": [],
  "missing_info": [],
  "confidence": 1.0,
  "breakdown": {
    "base_hours": 3.0,
    "multiplier": 1.2,
    "extra_hours": 1.0,
    "total_cleaner_hours": 4.6,
    "min_cleaner_hours_applied": 3.0,
    "team_size": 2,
    "time_on_site_hours": 2.5,
    "billed_cleaner_hours": 5.0,
    "labor_cost": 175.0,
    "add_ons_cost": 90.0,
    "discount_amount": 17.5,
    "total_before_tax": 247.5
  }
}
```

## Chat turn API

```bash
curl -X POST http://localhost:8000/v1/chat/turn \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "session-123",
    "message": "Hi, I need a deep clean for a 2 bed 1.5 bath with oven and fridge weekly"
  }'
```

## Leads API (Sprint 2)

```bash
curl -X POST http://localhost:8000/v1/leads \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "session-123",
    "contact_name": "Jane Doe",
    "email": "jane@example.com",
    "phone": "780-555-1234",
    "notes": "Call after 5pm"
  }'
```

Sample response:

```json
{
  "lead_id": "e1e955ad-1a2c-45d3-9e4d-6d1f82f9d7ab",
  "session_id": "session-123",
  "status": "received"
}
```

Sample response:

```json
{
  "session_id": "session-123",
  "intent": "QUOTE",
  "parsed_fields": {
    "beds": 2,
    "baths": 1.5,
    "cleaning_type": "deep",
    "heavy_grease": null,
    "multi_floor": null,
    "frequency": "weekly",
    "add_ons": {
      "oven": true,
      "fridge": true,
      "microwave": false,
      "cabinets": false,
      "windows_up_to_5": false,
      "balcony": false,
      "linen_beds": 0,
      "steam_armchair": 0,
      "steam_sofa_2": 0,
      "steam_sofa_3": 0,
      "steam_sectional": 0,
      "steam_mattress": 0,
      "carpet_spot": 0
    }
  },
  "state": {
    "beds": 2,
    "baths": 1.5,
    "cleaning_type": "deep",
    "heavy_grease": null,
    "multi_floor": null,
    "frequency": "weekly",
    "add_ons": {
      "oven": true,
      "fridge": true,
      "microwave": false,
      "cabinets": false,
      "windows_up_to_5": false,
      "balcony": false,
      "linen_beds": 0,
      "steam_armchair": 0,
      "steam_sofa_2": 0,
      "steam_sofa_3": 0,
      "steam_sectional": 0,
      "steam_mattress": 0,
      "carpet_spot": 0
    }
  },
  "missing_fields": [],
  "proposed_questions": [
    "What date and time window would you prefer?",
    "What is the service address postal code or area?"
  ],
  "reply_text": "Great news! Here's your Economy estimate: $207.50 before tax. Labor: $175.00, Add-ons: $50.00, Discounts: -$17.50. Team size 2, time on site 2.5h. Would you like to book a slot?",
  "handoff_required": false,
  "estimate": {
    "pricing_config_id": "economy_v1",
    "pricing_config_version": 1,
    "config_hash": "...",
    "rate": 35.0,
    "team_size": 1,
    "time_on_site_hours": 4.0,
    "billed_cleaner_hours": 4.0,
    "labor_cost": 140.0,
    "discount_amount": 14.0,
    "add_ons_cost": 50.0,
    "total_before_tax": 176.0,
    "assumptions": [],
    "missing_info": [],
    "confidence": 1.0,
    "breakdown": {
      "base_hours": 3.0,
      "multiplier": 1.2,
      "extra_hours": 0.0,
      "total_cleaner_hours": 3.6,
      "min_cleaner_hours_applied": 3.0,
      "team_size": 1,
      "time_on_site_hours": 4.0,
      "billed_cleaner_hours": 4.0,
      "labor_cost": 140.0,
      "add_ons_cost": 50.0,
      "discount_amount": 14.0,
      "total_before_tax": 176.0
    }
  },
  "confidence": 1.0
}
```

## Error format (ProblemDetails)

```json
{
  "type": "about:blank",
  "title": "Validation Error",
  "status": 422,
  "detail": "Request validation failed",
  "request_id": "8e3b0b5f-63c7-4596-9a9b-e4b6f1e5b6b0",
  "errors": [
    {
      "field": "beds",
      "message": "Input should be greater than or equal to 0"
    }
  ]
}
```

## Tests

```bash
pytest
```
