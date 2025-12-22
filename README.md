# Clean
curl -s http://localhost:8000/v1/estimate \
  -H 'Content-Type: application/json' \
  -d '{
    "beds":2,"baths":2,"cleaning_type":"deep",
    "heavy_grease":true,"multi_floor":false,
    "frequency":"biweekly",
    "add_ons":["inside_oven","inside_fridge"],
    "beds_with_linen_change":0
  }' | jq

curl -s http://localhost:8000/v1/chat/turn \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id":"11111111-1111-1111-1111-111111111111",
    "message":"Need deep clean 2 bed 2 bath, oven and fridge this weekend",
    "brand":"economy",
    "channel":"web",
    "client_context":{"tz":"America/Edmonton","locale":"en-CA"}
  }' | jq


curl -s http://localhost:8000/v1/leads \
  -H 'Content-Type: application/json' \
  -d '{
    "name":"Alex",
    "phone":"+1-780-555-0123",
    "email":"alex@example.com",
    "postal_code":"T5J",
    "preferred_dates":["2025-12-27 afternoon"],
    "access_notes":"I will be home",
    "parking":"street",
    "pets":false,
    "allergies":false,
    "notes":"Focus on kitchen",
    "structured_inputs":{"beds":2,"baths":2,"cleaning_type":"deep","heavy_grease":true,"multi_floor":false,"frequency":"biweekly","add_ons":["inside_oven"],"beds_with_linen_change":0},
    "estimate_snapshot":{"pricing_config_id":"economy","pricing_config_version":"v1","config_hash":"sha256:...","rate":35,"team_size":2,"time_on_site_hours":3.5,"billed_cleaner_hours":7,"labor_cost":245,"discount_amount":12.25,"add_ons_cost":30,"total_before_tax":262.75,"assumptions":[],"missing_info":[],"confidence":1.0},
    "utm":{"utm_source":"google","utm_campaign":"eco-edm"}
  }' | jq
