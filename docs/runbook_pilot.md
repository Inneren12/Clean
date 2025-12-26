# Pilot Runbook (Edmonton)

This runbook captures the operational steps for the Sprint A pilot.

## Viewing leads

- **Sheets/CRM (primary):** continue to rely on the Make/Zapier webhook export. Failed deliveries are logged with `lead_id` and `last_error_code` in application logs.
- **Admin API (backup):**
  - Endpoint: `GET /v1/admin/leads?status=NEW&limit=50`
  - Auth: HTTP Basic using `ADMIN_BASIC_USERNAME` / `ADMIN_BASIC_PASSWORD`
  - Response: latest leads with identifiers, contact info, preferred dates, notes, and referrers.
  - Best used from a secure terminal: `curl -u "$ADMIN_BASIC_USERNAME:$ADMIN_BASIC_PASSWORD" "$API_BASE/v1/admin/leads" | jq`.

## Resending the confirmation email

Emails are feature-flagged via `EMAIL_MODE`:
- `off`: disabled (default)
- `sendgrid`: SendGrid API
- `smtp`: SMTP relay

Required secrets:
- Common: `EMAIL_FROM`, `EMAIL_FROM_NAME`
- SendGrid: `SENDGRID_API_KEY`
- SMTP: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_USE_TLS`

To manually resend a confirmation email, run a one-off script inside the API environment with an optional `LEAD_ID` override:

```bash
python - <<'PY'
import asyncio
from sqlalchemy import select
import os

from app.domain.leads.db_models import Lead
from app.infra.db import _get_session_factory  # type: ignore
from app.infra.email import EmailAdapter


async def main():
    session_factory = _get_session_factory()
    lead_id = os.getenv("LEAD_ID")
    async with session_factory() as session:
        lead = None
        if lead_id:
            lead = await session.get(Lead, lead_id)
        if lead is None:
            result = await session.execute(select(Lead).order_by(Lead.created_at.desc()).limit(1))
            lead = result.scalar_one()
    await EmailAdapter().send_request_received(lead)


asyncio.run(main())
PY
```

Lead creation returns `201` while email/export delivery remains best-effort and may fail independently.

## Rotating secrets

- Update the `.env` (local) or the environment variables in the deploy target (Cloudflare/BaaS) for:
  - `SENDGRID_API_KEY` or SMTP credentials
  - `ADMIN_BASIC_USERNAME` / `ADMIN_BASIC_PASSWORD`
  - Webhook secrets (`EXPORT_WEBHOOK_URL`)
- Restart the API service (Docker `docker-compose up --build` locally or redeploy in CI) to apply new values.
- Validate by hitting `/health` and, if applicable, sending a test lead to confirm email/export delivery.
