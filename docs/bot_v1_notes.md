# Bot v1 hardening notes

- **Naming policy:** External bot API requests/responses use `camelCase` via Pydantic aliases while internal Python attributes remain `snake_case`. Inputs still accept snake_case thanks to `populate_by_name=True` for backward compatibility.
- **Logging shape:** Structured logs are flattened with keys like `request_id`, `conversation_id`, `intent`, `confidence`, and `fsm_step` to match `app.infra.logging.configure_logging` expectations (no nested `extra` keys).
- **Routes audit:** Existing leads endpoints live under `/v1/leads` and `/v1/admin/leads`, so the bot-specific `/api/leads` and `/api/cases` remain non-conflicting. Bot routes are grouped under `/api/bot/*` for session, message, and debugging reads.
- **Storage status:** The bot currently runs against `InMemoryBotStore`; Firestore schema and rules are kept as drafts until a Firestore-backed store ships.
