# QA — Admin & Dispatcher v1

## Roles
- Admin credentials: `ADMIN_BASIC_USERNAME` / `ADMIN_BASIC_PASSWORD` (full access).
- Dispatcher credentials: `DISPATCHER_BASIC_USERNAME` / `DISPATCHER_BASIC_PASSWORD` (leads + bookings, no pricing reload).

## Endpoints checklist
- `/v1/admin/leads` (list/filter) — admin or dispatcher.
- `/v1/admin/leads/{lead_id}/status` — admin or dispatcher; enforces lead status transitions.
- `/v1/admin/bookings` — admin or dispatcher; day/week filtering by `from`/`to`.
- `/v1/admin/bookings/{id}/confirm|cancel|reschedule` — admin or dispatcher; booking transitions respected and slots checked.
- `/v1/admin/bookings/{id}/resend-last-email` — admin or dispatcher.
- `/v1/admin/email-scan` — admin or dispatcher.
- `/v1/admin/pricing/reload` — admin only (dispatcher receives 403).

## Manual QA steps
1. Set both admin and dispatcher credentials in environment and restart the API.
2. Create a lead via the public `/v1/leads` endpoint.
3. Create a booking for that lead (POST `/v1/bookings`).
4. Authenticate as dispatcher:
   - List leads and update status to `CONTACTED`.
   - Confirm and reschedule the booking; ensure the new time is accepted.
   - Cancel the booking; invalid transitions should return 400.
5. Authenticate as dispatcher and call `/v1/admin/pricing/reload` → expect 403.
6. Authenticate as admin and call `/v1/admin/pricing/reload` → expect 202.
7. Visit `/admin` in the Next.js app, save credentials, and confirm leads/bookings render with day/week views and actions.
