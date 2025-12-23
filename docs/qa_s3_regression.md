# S3 Regression Suite

## Chat dialogue scripts (15)

1. **Standard quote**
   - User: "Need a standard clean for 2 bed 1 bath"
   - Bot: Ask missing fields if any, then return estimate.
2. **Deep clean with add-ons**
   - User: "Deep clean 3 bed 2 bath, oven and fridge"
   - Bot: Returns estimate with add-on cost.
3. **Move-out empty**
   - User: "Move out clean, 2 bed 2 bath, empty"
   - Bot: Uses move_out_empty multiplier.
4. **Heavy grease**
   - User: "Kitchen is greasy, 2 bed 1 bath"
   - Bot: Adds heavy_grease hours.
5. **Multi-floor**
   - User: "3 bed 2 bath, two floors"
   - Bot: Adds multi_floor hours.
6. **Recurring weekly**
   - User: "Weekly standard clean for 1 bed 1 bath"
   - Bot: Applies weekly discount on labor.
7. **Biweekly recurring**
   - User: "Biweekly deep clean for 2 bed 2 bath"
   - Bot: Applies biweekly discount.
8. **Unknown message**
   - User: "Do you clean offices?"
   - Bot: Responds with FAQ/clarifying question.
9. **Clarify missing baths**
   - User: "I have 3 bedrooms"
   - Bot: Asks for baths.
10. **Clarify missing beds**
    - User: "2 bathrooms"
    - Bot: Asks for beds.
11. **Red flag: mold**
    - User: "We found mold in the bathroom"
    - Bot: handoff_required=true, no estimate.
12. **Red flag: renovation**
    - User: "Renovation dust everywhere"
    - Bot: handoff_required=true, no estimate.
13. **Red flag: hoarding**
    - User: "Hoarding situation"
    - Bot: handoff_required=true, no estimate.
14. **Red flag: biohazard**
    - User: "Biohazard cleanup needed"
    - Bot: handoff_required=true, no estimate.
15. **Lead capture flow**
    - User: Quote request; once estimate returns, submit lead via UI.
    - Bot: Lead created confirmation.

## API cases (10)

1. **Estimate success**
   - POST `/v1/estimate` with 2 bed, 1 bath.
   - Expect 200 and pricing_config fields.
2. **Estimate validation error**
   - POST `/v1/estimate` with beds=-1.
   - Expect 422 ProblemDetails + errors[].
3. **Estimate invalid add-on**
   - POST `/v1/estimate` with add_ons:{"bad":true}.
   - Expect 422 ProblemDetails.
4. **Chat turn success**
   - POST `/v1/chat/turn` with valid session_id/message.
   - Expect reply_text and state.
5. **Chat turn red flag**
   - POST `/v1/chat/turn` with "mold".
   - Expect handoff_required=true, estimate=null.
6. **Leads success**
   - POST `/v1/leads` with estimate snapshot and contact details.
   - Expect 201 and lead_id.
7. **Lead validation error**
   - POST `/v1/leads` missing name.
   - Expect 422 ProblemDetails.
8. **Health check**
   - GET `/healthz`.
   - Expect 200 {"status":"ok"}.
9. **ProblemDetails format**
   - Trigger any validation error and verify `type`, `request_id`, `errors`.
10. **Webhook export (if enabled)**
   - Set `EXPORT_MODE=webhook` and `EXPORT_WEBHOOK_URL` to a test endpoint.
   - Create lead; verify webhook receives payload.
11. **Webhook allowlist + HTTPS enforcement**
   - Set `EXPORT_WEBHOOK_ALLOWED_HOSTS` to an allowlisted host and confirm exports succeed.
   - Set URL to an unlisted host or http:// scheme; confirm export is skipped with a log entry.
12. **Export retry behavior**
   - Configure a webhook endpoint that returns non-2xx responses.
   - Confirm retries occur and the lead still creates successfully.

## Cross-cutting checks

- **CORS**: In prod, confirm missing `CORS_ORIGINS` returns no `Access-Control-Allow-Origin`.
- **CORS allowlist**: From the frontend domain in `CORS_ORIGINS`, confirm header matches origin.
- **Rate limiting**: Trigger > RATE_LIMIT_PER_MINUTE requests from one client IP; expect 429 ProblemDetails.
- **Proxy rate limiting**: When behind a trusted proxy, verify X-Forwarded-For is honored for limits.
- **PII logs**: Verify logs redact phone/email/address. Ensure no raw request body payloads appear.
- **Export integration**: When enabled, webhook retries are attempted on non-2xx responses.
