# QA: export dead-letter

1. Set `EXPORT_MODE=webhook`, `EXPORT_WEBHOOK_URL=http://example.com/webhook`, `EXPORT_WEBHOOK_ALLOW_HTTP=true`, and `EXPORT_WEBHOOK_MAX_RETRIES` to a small number.
2. Use a mock transport or point to a failing URL so exports return non-2xx responses.
3. Create a lead via `POST /v1/leads` (captcha disabled by default). After retries are exhausted, the API logs an `export_webhook_failed` message.
4. Verify the dead-letter row is persisted:
   - Query `export_events` (ordered by `created_at` descending) or
   - Call `GET /v1/admin/export-dead-letter?limit=50` with admin/dispatcher credentials. Entries include `mode`, `target_url_host`, `attempts`, and `last_error_code` (no PII).
5. Clear or replay failed events as needed; inserts are best-effort and do not block lead creation.
